from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    Assignment,
    AuditEvent,
    Campaign,
    ContractTask,
    Dispute,
    EvidenceItem,
    EvidenceSubmission,
    Message,
    Review,
    TestingContract,
)
from app.models.enums import (
    AssignmentStatus,
    CreditEntryType,
    DisputeRemedy,
    DisputeStatus,
    SubmissionStatus,
)
from app.schemas.api import DisputeResolve
from app.services.campaigns import maybe_complete_campaign
from app.services.common import DomainError, add_audit_event, get_campaign
from app.services.credits import record_credit_entry
from app.services.notifications import add_notification


@dataclass(frozen=True, slots=True)
class ModerationCase:
    dispute: Dispute
    assignment: Assignment
    campaign: Campaign
    contract: TestingContract
    tasks: list[ContractTask]
    submissions: list[EvidenceSubmission]
    evidence_by_submission: dict[UUID, list[EvidenceItem]]
    reviews: list[Review]
    messages: list[Message]
    audit_events: list[AuditEvent]


def list_disputes(db: Session, status_filter: DisputeStatus | None) -> list[Dispute]:
    query = select(Dispute)
    if status_filter is not None:
        query = query.where(Dispute.status == status_filter)
    return list(db.scalars(query.order_by(Dispute.created_at, Dispute.id)))


def get_moderation_case(db: Session, dispute_id: UUID) -> ModerationCase:
    dispute = db.get(Dispute, dispute_id)
    if dispute is None:
        raise DomainError("Dispute not found", 404)
    assignment = db.get(Assignment, dispute.assignment_id)
    if assignment is None:
        raise DomainError("Assignment not found", 404)
    campaign = get_campaign(db, assignment.campaign_id)
    contract = db.scalar(select(TestingContract).where(TestingContract.campaign_id == campaign.id))
    if contract is None:
        raise DomainError("Testing contract not found", 404)

    tasks = list(
        db.scalars(
            select(ContractTask)
            .where(ContractTask.contract_id == contract.id)
            .order_by(ContractTask.position, ContractTask.id)
        )
    )
    submissions = list(
        db.scalars(
            select(EvidenceSubmission)
            .where(EvidenceSubmission.assignment_id == assignment.id)
            .order_by(EvidenceSubmission.version, EvidenceSubmission.id)
        )
    )
    submission_ids = [submission.id for submission in submissions]
    evidence_by_submission = {submission_id: [] for submission_id in submission_ids}
    reviews: list[Review] = []
    if submission_ids:
        evidence = db.scalars(
            select(EvidenceItem)
            .where(EvidenceItem.submission_id.in_(submission_ids))
            .order_by(EvidenceItem.created_at, EvidenceItem.id)
        )
        for item in evidence:
            evidence_by_submission[item.submission_id].append(item)
        reviews = list(
            db.scalars(
                select(Review)
                .where(Review.submission_id.in_(submission_ids))
                .order_by(Review.created_at, Review.id)
            )
        )

    messages = list(
        db.scalars(
            select(Message)
            .where(Message.assignment_id == assignment.id)
            .order_by(Message.created_at, Message.id)
        )
    )
    audit_events = list(
        db.scalars(
            select(AuditEvent)
            .where(AuditEvent.entity_type == "assignment", AuditEvent.entity_id == assignment.id)
            .order_by(AuditEvent.created_at, AuditEvent.id)
        )
    )
    return ModerationCase(
        dispute=dispute,
        assignment=assignment,
        campaign=campaign,
        contract=contract,
        tasks=tasks,
        submissions=submissions,
        evidence_by_submission=evidence_by_submission,
        reviews=reviews,
        messages=messages,
        audit_events=audit_events,
    )


def claim_dispute(db: Session, *, dispute_id: UUID, moderator_id: UUID) -> Dispute:
    dispute = db.scalar(select(Dispute).where(Dispute.id == dispute_id).with_for_update())
    if dispute is None:
        raise DomainError("Dispute not found", 404)
    if dispute.status == DisputeStatus.UNDER_REVIEW:
        if dispute.assigned_to == moderator_id:
            return dispute
        raise DomainError("This dispute is already assigned to another moderator", 409)
    if dispute.status != DisputeStatus.OPEN:
        raise DomainError("Only open disputes can be claimed", 409)

    dispute.status = DisputeStatus.UNDER_REVIEW
    dispute.assigned_to = moderator_id
    dispute.assigned_at = datetime.now(UTC)
    add_audit_event(
        db,
        actor_id=moderator_id,
        action="dispute.claimed",
        entity_type="assignment",
        entity_id=dispute.assignment_id,
        details={"dispute_id": str(dispute.id)},
    )
    db.flush()
    return dispute


def resolve_dispute(
    db: Session,
    *,
    dispute_id: UUID,
    moderator_id: UUID,
    payload: DisputeResolve,
) -> Dispute:
    dispute = db.scalar(select(Dispute).where(Dispute.id == dispute_id).with_for_update())
    if dispute is None:
        raise DomainError("Dispute not found", 404)
    if dispute.status != DisputeStatus.UNDER_REVIEW:
        raise DomainError("Claim this dispute before resolving it", 409)
    if dispute.assigned_to != moderator_id:
        raise DomainError("Only the assigned moderator can resolve this dispute", 403)

    assignment = db.scalar(
        select(Assignment).where(Assignment.id == dispute.assignment_id).with_for_update()
    )
    if assignment is None:
        raise DomainError("Assignment not found", 404)

    if payload.remedy == DisputeRemedy.AWARD_TESTER:
        if dispute.submission_id is None:
            raise DomainError("This dispute has no submission to approve", 409)
        if assignment.status != AssignmentStatus.REJECTED:
            raise DomainError("Only a rejected assignment can receive a dispute award", 409)
        submission = db.scalar(
            select(EvidenceSubmission)
            .where(EvidenceSubmission.id == dispute.submission_id)
            .with_for_update()
        )
        if submission is None or submission.assignment_id != assignment.id:
            raise DomainError("The disputed submission was not found", 409)
        campaign = db.scalar(
            select(Campaign).where(Campaign.id == assignment.campaign_id).with_for_update()
        )
        if campaign is None:
            raise DomainError("Campaign not found", 404)

        now = datetime.now(UTC)
        submission.status = SubmissionStatus.APPROVED
        assignment.status = AssignmentStatus.APPROVED
        assignment.completed_at = now
        record_credit_entry(
            db,
            user_id=assignment.tester_id,
            delta=campaign.reward_credits,
            entry_type=CreditEntryType.REWARD,
            idempotency_key=f"assignment:{assignment.id}:reward",
            reference_type="assignment",
            reference_id=assignment.id,
            note=f"Moderator-approved test for {campaign.name}",
            created_by=moderator_id,
        )
        db.flush()
        maybe_complete_campaign(db, campaign=campaign, actor_id=moderator_id)

    dispute.status = DisputeStatus(payload.outcome)
    dispute.resolution = payload.resolution
    dispute.remedy = payload.remedy
    dispute.resolved_by = moderator_id
    dispute.resolved_at = datetime.now(UTC)
    add_audit_event(
        db,
        actor_id=moderator_id,
        action=f"dispute.{payload.outcome}",
        entity_type="assignment",
        entity_id=dispute.assignment_id,
        details={
            "dispute_id": str(dispute.id),
            "outcome": payload.outcome,
            "remedy": payload.remedy.value,
        },
    )
    campaign_for_notification = get_campaign(db, assignment.campaign_id)
    resolution_body = payload.resolution[:500]
    for recipient_id, audience in (
        (assignment.tester_id, "tester"),
        (campaign_for_notification.owner_id, "owner"),
    ):
        add_notification(
            db,
            user_id=recipient_id,
            kind="dispute_resolved",
            title=f"Dispute resolved for {campaign_for_notification.name}",
            body=resolution_body,
            entity_type="dispute",
            entity_id=dispute.id,
            idempotency_key=f"dispute:{dispute.id}:{audience}-resolution-notification",
        )
    db.flush()
    return dispute
