from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.models import Assignment, Campaign, ContractTask, TestingContract
from app.models.enums import (
    ACTIVE_TESTING_ASSIGNMENT_STATUSES,
    OCCUPIED_ASSIGNMENT_STATUSES,
    AssignmentStatus,
    CampaignStatus,
    ContractStatus,
    CreditEntryType,
)
from app.schemas.api import (
    CampaignCreate,
    CampaignLaunch,
    CampaignTransition,
    CampaignUpdate,
    ContractUpsert,
)
from app.services.common import (
    DomainError,
    add_audit_event,
    get_campaign,
    get_profile,
    require_campaign_owner,
)
from app.services.credits import record_credit_entry
from app.services.notifications import add_notification


def create_campaign(db: Session, *, owner_id: UUID, payload: CampaignCreate) -> Campaign:
    get_profile(db, owner_id)
    if db.scalar(select(Campaign.id).where(Campaign.slug == payload.slug)) is not None:
        raise DomainError("That campaign slug is already in use", 409)
    campaign = Campaign(owner_id=owner_id, **payload.model_dump())
    db.add(campaign)
    db.flush()
    add_audit_event(
        db,
        actor_id=owner_id,
        action="campaign.created",
        entity_type="campaign",
        entity_id=campaign.id,
    )
    return campaign


def update_campaign(
    db: Session, *, campaign_id: UUID, owner_id: UUID, payload: CampaignUpdate
) -> Campaign:
    campaign = get_campaign(db, campaign_id)
    require_campaign_owner(campaign, owner_id)
    if campaign.status != CampaignStatus.DRAFT:
        raise DomainError("Published campaigns cannot change their testing promise", 409)
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(campaign, field, value)
    add_audit_event(
        db,
        actor_id=owner_id,
        action="campaign.updated",
        entity_type="campaign",
        entity_id=campaign.id,
    )
    db.flush()
    return campaign


def list_public_campaigns(db: Session) -> list[Campaign]:
    occupied_slots = (
        select(func.count(Assignment.id))
        .where(
            Assignment.campaign_id == Campaign.id,
            Assignment.status.in_(OCCUPIED_ASSIGNMENT_STATUSES),
        )
        .correlate(Campaign)
        .scalar_subquery()
    )
    return list(
        db.scalars(
            select(Campaign)
            .where(
                Campaign.status == CampaignStatus.PUBLISHED,
                occupied_slots < Campaign.target_testers,
            )
            .order_by(Campaign.published_at.desc())
        )
    )


def list_owned_campaigns(db: Session, owner_id: UUID) -> list[Campaign]:
    return list(
        db.scalars(
            select(Campaign)
            .where(Campaign.owner_id == owner_id)
            .order_by(Campaign.created_at.desc())
        )
    )


def upsert_contract(
    db: Session,
    *,
    campaign_id: UUID,
    owner_id: UUID,
    payload: ContractUpsert,
) -> TestingContract:
    campaign = get_campaign(db, campaign_id)
    require_campaign_owner(campaign, owner_id)
    if campaign.status != CampaignStatus.DRAFT:
        raise DomainError("The contract is locked after publication", 409)

    contract = db.scalar(select(TestingContract).where(TestingContract.campaign_id == campaign_id))
    contract_values = payload.model_dump(exclude={"tasks"})
    if contract is None:
        contract = TestingContract(campaign_id=campaign_id, **contract_values)
        db.add(contract)
        db.flush()
    else:
        if contract.status == ContractStatus.LOCKED:
            raise DomainError("The contract is locked", 409)
        contract.version += 1
        for field, value in contract_values.items():
            setattr(contract, field, value)
        db.execute(delete(ContractTask).where(ContractTask.contract_id == contract.id))

    for position, task in enumerate(payload.tasks):
        db.add(ContractTask(contract_id=contract.id, position=position, **task.model_dump()))
    add_audit_event(
        db,
        actor_id=owner_id,
        action="contract.saved",
        entity_type="campaign",
        entity_id=campaign_id,
        details={"contract_id": str(contract.id), "version": contract.version},
    )
    db.flush()
    return contract


def get_contract(db: Session, campaign_id: UUID) -> tuple[TestingContract, list[ContractTask]]:
    contract = db.scalar(select(TestingContract).where(TestingContract.campaign_id == campaign_id))
    if contract is None:
        raise DomainError("Testing contract not found", 404)
    tasks = list(
        db.scalars(
            select(ContractTask)
            .where(ContractTask.contract_id == contract.id)
            .order_by(ContractTask.position)
        )
    )
    return contract, tasks


def publish_campaign(db: Session, *, campaign_id: UUID, owner_id: UUID) -> Campaign:
    campaign = db.scalar(select(Campaign).where(Campaign.id == campaign_id).with_for_update())
    if campaign is None:
        raise DomainError("Campaign not found", 404)
    require_campaign_owner(campaign, owner_id)
    if campaign.status == CampaignStatus.PUBLISHED:
        return campaign
    if campaign.status != CampaignStatus.DRAFT:
        raise DomainError("Only draft campaigns can be published", 409)

    contract, tasks = get_contract(db, campaign_id)
    if not tasks:
        raise DomainError("Add at least one contract task before publishing", 409)

    required_credits = campaign.target_testers * campaign.reward_credits
    record_credit_entry(
        db,
        user_id=owner_id,
        delta=-required_credits,
        entry_type=CreditEntryType.POSTING,
        idempotency_key=f"campaign:{campaign.id}:posting",
        reference_type="campaign",
        reference_id=campaign.id,
        note=(
            f"Published {campaign.name}: permanent spend for "
            f"{campaign.target_testers} tester rewards"
        ),
        created_by=owner_id,
    )
    now = datetime.now(UTC)
    campaign.status = CampaignStatus.PUBLISHED
    campaign.published_at = now
    contract.status = ContractStatus.LOCKED
    contract.locked_at = now
    add_audit_event(
        db,
        actor_id=owner_id,
        action="campaign.published",
        entity_type="campaign",
        entity_id=campaign.id,
        details={"spent_credits": required_credits, "refundable": False},
    )
    db.flush()
    return campaign


def launch_campaign(db: Session, *, owner_id: UUID, payload: CampaignLaunch) -> Campaign:
    """Create, lock, fund, and publish a campaign in one database transaction."""
    campaign = create_campaign(db, owner_id=owner_id, payload=payload.campaign)
    upsert_contract(
        db,
        campaign_id=campaign.id,
        owner_id=owner_id,
        payload=payload.contract,
    )
    return publish_campaign(db, campaign_id=campaign.id, owner_id=owner_id)


def maybe_complete_campaign(db: Session, *, campaign: Campaign, actor_id: UUID) -> bool:
    approved_count = (
        db.scalar(
            select(func.count(Assignment.id)).where(
                Assignment.campaign_id == campaign.id,
                Assignment.status == AssignmentStatus.APPROVED,
            )
        )
        or 0
    )
    target_reached = (
        campaign.status in {CampaignStatus.PUBLISHED, CampaignStatus.PAUSED}
        and approved_count >= campaign.target_testers
    )
    closed_work_settled = False
    if campaign.status == CampaignStatus.CANCELLED:
        active_count = (
            db.scalar(
                select(func.count(Assignment.id)).where(
                    Assignment.campaign_id == campaign.id,
                    Assignment.status.in_(ACTIVE_TESTING_ASSIGNMENT_STATUSES),
                )
            )
            or 0
        )
        accepted_count = (
            db.scalar(
                select(func.count(Assignment.id)).where(
                    Assignment.campaign_id == campaign.id,
                    Assignment.accepted_at.is_not(None),
                )
            )
            or 0
        )
        closed_work_settled = active_count == 0 and accepted_count > 0
    if not target_reached and not closed_work_settled:
        return False

    campaign.status = CampaignStatus.COMPLETED
    add_audit_event(
        db,
        actor_id=actor_id,
        action="campaign.completed",
        entity_type="campaign",
        entity_id=campaign.id,
        details={
            "approved_testers": approved_count,
            "target_reached": target_reached,
            "recruitment_closed": closed_work_settled,
        },
    )
    return True


def transition_campaign(
    db: Session,
    *,
    campaign_id: UUID,
    owner_id: UUID,
    payload: CampaignTransition,
) -> Campaign:
    campaign = db.scalar(select(Campaign).where(Campaign.id == campaign_id).with_for_update())
    if campaign is None:
        raise DomainError("Campaign not found", 404)
    require_campaign_owner(campaign, owner_id)

    previous_status = campaign.status
    if payload.action == "pause":
        if campaign.status != CampaignStatus.PUBLISHED:
            raise DomainError("Only published campaigns can be paused", 409)
        campaign.status = CampaignStatus.PAUSED
    elif payload.action == "resume":
        if campaign.status != CampaignStatus.PAUSED:
            raise DomainError("Only paused campaigns can be resumed", 409)
        occupied_slots = db.scalar(
            select(func.count(Assignment.id)).where(
                Assignment.campaign_id == campaign.id,
                Assignment.status.in_(OCCUPIED_ASSIGNMENT_STATUSES),
            )
        )
        if occupied_slots is not None and occupied_slots >= campaign.target_testers:
            raise DomainError("This campaign already has its target number of testers", 409)
        campaign.status = CampaignStatus.PUBLISHED
    else:
        if campaign.status not in {CampaignStatus.PUBLISHED, CampaignStatus.PAUSED}:
            raise DomainError("Only an open or paused campaign can be closed", 409)
        campaign.status = CampaignStatus.CANCELLED
        pending_assignments = list(
            db.scalars(
                select(Assignment)
                .where(
                    Assignment.campaign_id == campaign.id,
                    Assignment.status == AssignmentStatus.APPLIED,
                )
                .with_for_update()
            )
        )
        now = datetime.now(UTC)
        for assignment in pending_assignments:
            assignment.status = AssignmentStatus.CANCELLED
            assignment.completed_at = now
            add_audit_event(
                db,
                actor_id=owner_id,
                action="assignment.declined",
                entity_type="assignment",
                entity_id=assignment.id,
                details={"reason": "recruitment_closed"},
            )
            add_notification(
                db,
                user_id=assignment.tester_id,
                kind="application_closed",
                title=f"Recruitment closed for {campaign.name}",
                body="The campaign closed recruitment before this application was accepted.",
                entity_type="assignment",
                entity_id=assignment.id,
                idempotency_key=f"assignment:{assignment.id}:recruitment-closed-notification",
            )
    add_audit_event(
        db,
        actor_id=owner_id,
        action=f"campaign.{payload.action}d" if payload.action != "close" else "campaign.closed",
        entity_type="campaign",
        entity_id=campaign.id,
        details={
            "previous_status": previous_status.value,
            "new_status": campaign.status.value,
            "refund_credits": 0,
        },
    )
    if payload.action == "close":
        maybe_complete_campaign(db, campaign=campaign, actor_id=owner_id)
    db.flush()
    return campaign
