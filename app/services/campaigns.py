from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.models import Campaign, ContractTask, TestingContract
from app.models.enums import CampaignStatus, ContractStatus, CreditEntryType
from app.schemas.api import CampaignCreate, CampaignUpdate, ContractUpsert
from app.services.common import (
    DomainError,
    add_audit_event,
    get_campaign,
    get_profile,
    require_campaign_owner,
)
from app.services.credits import record_credit_entry


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
    return list(
        db.scalars(
            select(Campaign)
            .where(Campaign.status == CampaignStatus.PUBLISHED)
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
        entry_type=CreditEntryType.RESERVATION,
        idempotency_key=f"campaign:{campaign.id}:reservation",
        reference_type="campaign",
        reference_id=campaign.id,
        note=f"Reserved for {campaign.target_testers} tester rewards",
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
        details={"reserved_credits": required_credits},
    )
    db.flush()
    return campaign
