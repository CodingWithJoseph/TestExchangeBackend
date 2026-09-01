from datetime import UTC, datetime, timedelta
from pathlib import PurePosixPath
from uuid import UUID

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.models import (
    Assignment,
    Campaign,
    ContractTask,
    Dispute,
    EvidenceItem,
    EvidenceSubmission,
    Message,
    Review,
    TestingContract,
    TestingSession,
)
from app.models.enums import (
    OCCUPIED_ASSIGNMENT_STATUSES,
    AssignmentStatus,
    CampaignStatus,
    CreditEntryType,
    ReviewDecision,
    SubmissionStatus,
)
from app.schemas.api import (
    AssignmentApply,
    DisputeCreate,
    MessageCreate,
    ReviewCreate,
    SubmissionCreate,
    TestingSessionCreate,
)
from app.services.campaigns import maybe_complete_campaign
from app.services.common import (
    DomainError,
    add_audit_event,
    get_assignment,
    get_campaign,
    get_profile,
    require_assignment_participant,
    require_campaign_owner,
)
from app.services.credits import record_credit_entry
from app.services.notifications import add_notification


def apply_to_campaign(
    db: Session, *, campaign_id: UUID, tester_id: UUID, payload: AssignmentApply
) -> Assignment:
    get_profile(db, tester_id)
    campaign = db.scalar(select(Campaign).where(Campaign.id == campaign_id).with_for_update())
    if campaign is None:
        raise DomainError("Campaign not found", 404)
    if campaign.status != CampaignStatus.PUBLISHED:
        raise DomainError("This campaign is not accepting testers", 409)
    if campaign.owner_id == tester_id:
        raise DomainError("Campaign owners cannot test their own campaign", 409)
    existing = db.scalar(
        select(Assignment).where(
            Assignment.campaign_id == campaign_id, Assignment.tester_id == tester_id
        )
    )
    if existing is not None:
        raise DomainError("You already applied to this campaign", 409)
    occupied_slots = db.scalar(
        select(func.count(Assignment.id)).where(
            Assignment.campaign_id == campaign.id,
            Assignment.status.in_(OCCUPIED_ASSIGNMENT_STATUSES),
        )
    )
    if occupied_slots is not None and occupied_slots >= campaign.target_testers:
        raise DomainError("This campaign already has its target number of testers", 409)

    assignment = Assignment(
        campaign_id=campaign_id,
        tester_id=tester_id,
        application_note=payload.application_note,
    )
    db.add(assignment)
    db.flush()
    add_audit_event(
        db,
        actor_id=tester_id,
        action="assignment.applied",
        entity_type="assignment",
        entity_id=assignment.id,
    )
    add_notification(
        db,
        user_id=campaign.owner_id,
        kind="application_received",
        title=f"New application for {campaign.name}",
        body=f"{assignment.tester_profile.display_name} requested a tester spot.",
        entity_type="assignment",
        entity_id=assignment.id,
        idempotency_key=f"assignment:{assignment.id}:application-notification",
    )
    return assignment


def list_user_assignments(db: Session, user_id: UUID) -> list[Assignment]:
    return list(
        db.scalars(
            select(Assignment)
            .join(Campaign, Campaign.id == Assignment.campaign_id)
            .where(or_(Assignment.tester_id == user_id, Campaign.owner_id == user_id))
            .order_by(Assignment.created_at.desc())
        )
    )


def accept_assignment(db: Session, *, assignment_id: UUID, owner_id: UUID) -> Assignment:
    assignment_campaign_id = db.scalar(
        select(Assignment.campaign_id).where(Assignment.id == assignment_id)
    )
    if assignment_campaign_id is None:
        raise DomainError("Assignment not found", 404)
    # Every accept for the same campaign serializes on this row before capacity is counted.
    campaign = db.scalar(
        select(Campaign).where(Campaign.id == assignment_campaign_id).with_for_update()
    )
    if campaign is None:
        raise DomainError("Campaign not found", 404)
    require_campaign_owner(campaign, owner_id)
    if campaign.status != CampaignStatus.PUBLISHED:
        raise DomainError("This campaign is not accepting testers", 409)
    assignment = db.scalar(
        select(Assignment).where(Assignment.id == assignment_id).with_for_update()
    )
    if assignment is None:
        raise DomainError("Assignment not found", 404)
    if assignment.status != AssignmentStatus.APPLIED:
        raise DomainError("Only pending applications can be accepted", 409)

    occupied_slots = db.scalar(
        select(func.count(Assignment.id)).where(
            Assignment.campaign_id == campaign.id,
            Assignment.status.in_(OCCUPIED_ASSIGNMENT_STATUSES),
        )
    )
    if occupied_slots is not None and occupied_slots >= campaign.target_testers:
        raise DomainError("This campaign already has its target number of testers", 409)

    assignment.status = AssignmentStatus.ACCEPTED
    assignment.accepted_at = datetime.now(UTC)
    add_audit_event(
        db,
        actor_id=owner_id,
        action="assignment.accepted",
        entity_type="assignment",
        entity_id=assignment.id,
    )
    add_notification(
        db,
        user_id=assignment.tester_id,
        kind="application_accepted",
        title=f"You were accepted for {campaign.name}",
        body="The locked contract and private access instructions are now available.",
        entity_type="assignment",
        entity_id=assignment.id,
        idempotency_key=f"assignment:{assignment.id}:accepted-notification",
    )
    db.flush()
    return assignment


def decline_assignment(db: Session, *, assignment_id: UUID, owner_id: UUID) -> Assignment:
    assignment = db.scalar(
        select(Assignment).where(Assignment.id == assignment_id).with_for_update()
    )
    if assignment is None:
        raise DomainError("Assignment not found", 404)
    campaign = get_campaign(db, assignment.campaign_id)
    require_campaign_owner(campaign, owner_id)
    if assignment.status != AssignmentStatus.APPLIED:
        raise DomainError("Only pending applications can be declined", 409)
    assignment.status = AssignmentStatus.CANCELLED
    assignment.completed_at = datetime.now(UTC)
    add_audit_event(
        db,
        actor_id=owner_id,
        action="assignment.declined",
        entity_type="assignment",
        entity_id=assignment.id,
    )
    add_notification(
        db,
        user_id=assignment.tester_id,
        kind="application_declined",
        title=f"Application update for {campaign.name}",
        body="The campaign owner selected other testers for this campaign.",
        entity_type="assignment",
        entity_id=assignment.id,
        idempotency_key=f"assignment:{assignment.id}:declined-notification",
    )
    db.flush()
    return assignment


def withdraw_assignment(db: Session, *, assignment_id: UUID, tester_id: UUID) -> Assignment:
    assignment = db.scalar(
        select(Assignment).where(Assignment.id == assignment_id).with_for_update()
    )
    if assignment is None:
        raise DomainError("Assignment not found", 404)
    if assignment.tester_id != tester_id:
        raise DomainError("Only the tester can withdraw from this assignment", 403)
    if assignment.status not in {
        AssignmentStatus.APPLIED,
        AssignmentStatus.ACCEPTED,
        AssignmentStatus.IN_PROGRESS,
        AssignmentStatus.CHANGES_REQUESTED,
    }:
        raise DomainError("This assignment can no longer be withdrawn", 409)
    campaign = db.scalar(
        select(Campaign).where(Campaign.id == assignment.campaign_id).with_for_update()
    )
    if campaign is None:
        raise DomainError("Campaign not found", 404)
    previous_status = assignment.status
    assignment.status = AssignmentStatus.CANCELLED
    assignment.completed_at = datetime.now(UTC)
    add_audit_event(
        db,
        actor_id=tester_id,
        action="assignment.withdrawn",
        entity_type="assignment",
        entity_id=assignment.id,
        details={"previous_status": previous_status.value},
    )
    add_notification(
        db,
        user_id=campaign.owner_id,
        kind="tester_withdrew",
        title=f"A tester withdrew from {campaign.name}",
        body=f"{assignment.tester_profile.display_name} is no longer participating.",
        entity_type="assignment",
        entity_id=assignment.id,
        idempotency_key=f"assignment:{assignment.id}:withdrawn-notification",
    )
    maybe_complete_campaign(db, campaign=campaign, actor_id=tester_id)
    db.flush()
    return assignment


def start_assignment(db: Session, *, assignment_id: UUID, tester_id: UUID) -> Assignment:
    assignment = get_assignment(db, assignment_id)
    if assignment.tester_id != tester_id:
        raise DomainError("Only the assigned tester can start this test", 403)
    if assignment.status != AssignmentStatus.ACCEPTED:
        raise DomainError("Only accepted assignments can be started", 409)
    now = datetime.now(UTC)
    assignment.status = AssignmentStatus.IN_PROGRESS
    assignment.started_at = now
    db.add(
        TestingSession(
            assignment_id=assignment.id,
            session_date=now.date(),
            note="Testing started",
        )
    )
    add_audit_event(
        db,
        actor_id=tester_id,
        action="assignment.started",
        entity_type="assignment",
        entity_id=assignment.id,
    )
    db.flush()
    return assignment


def _contract_for_assignment(
    db: Session, assignment: Assignment
) -> tuple[TestingContract, list[ContractTask]]:
    contract = db.scalar(
        select(TestingContract).where(TestingContract.campaign_id == assignment.campaign_id)
    )
    if contract is None:
        raise DomainError("The campaign does not have a testing contract", 409)
    tasks = list(db.scalars(select(ContractTask).where(ContractTask.contract_id == contract.id)))
    return contract, tasks


def record_testing_session(
    db: Session,
    *,
    assignment_id: UUID,
    tester_id: UUID,
    payload: TestingSessionCreate,
) -> TestingSession:
    assignment = db.scalar(
        select(Assignment).where(Assignment.id == assignment_id).with_for_update()
    )
    if assignment is None:
        raise DomainError("Assignment not found", 404)
    if assignment.tester_id != tester_id:
        raise DomainError("Only the assigned tester can record a session", 403)
    if assignment.status not in {
        AssignmentStatus.IN_PROGRESS,
        AssignmentStatus.CHANGES_REQUESTED,
    }:
        raise DomainError("Sessions can only be recorded while testing is active", 409)
    today = datetime.now(UTC).date()
    existing = db.scalar(
        select(TestingSession.id).where(
            TestingSession.assignment_id == assignment_id,
            TestingSession.session_date == today,
        )
    )
    if existing is not None:
        raise DomainError("A testing session is already recorded for today", 409)
    session = TestingSession(
        assignment_id=assignment_id,
        session_date=today,
        note=payload.note,
    )
    db.add(session)
    db.flush()
    add_audit_event(
        db,
        actor_id=tester_id,
        action="testing_session.recorded",
        entity_type="assignment",
        entity_id=assignment.id,
        details={"testing_session_id": str(session.id), "session_date": today.isoformat()},
    )
    return session


def list_testing_sessions(
    db: Session, *, assignment_id: UUID, user_id: UUID
) -> list[TestingSession]:
    assignment = get_assignment(db, assignment_id)
    require_assignment_participant(db, assignment, user_id)
    return list(
        db.scalars(
            select(TestingSession)
            .where(TestingSession.assignment_id == assignment_id)
            .order_by(TestingSession.session_date, TestingSession.created_at)
        )
    )


def create_submission(
    db: Session, *, assignment_id: UUID, tester_id: UUID, payload: SubmissionCreate
) -> EvidenceSubmission:
    assignment = db.scalar(
        select(Assignment).where(Assignment.id == assignment_id).with_for_update()
    )
    if assignment is None:
        raise DomainError("Assignment not found", 404)
    if assignment.tester_id != tester_id:
        raise DomainError("Only the assigned tester can submit evidence", 403)
    if assignment.status not in {
        AssignmentStatus.IN_PROGRESS,
        AssignmentStatus.CHANGES_REQUESTED,
    }:
        raise DomainError("This assignment is not ready for a submission", 409)

    contract, tasks = _contract_for_assignment(db, assignment)
    now = datetime.now(UTC)
    if assignment.started_at is None:
        raise DomainError("Start this assignment before submitting evidence", 409)
    started_at = assignment.started_at
    if started_at.tzinfo is None:
        started_at = started_at.replace(tzinfo=UTC)
    earliest_submission = started_at + timedelta(days=contract.minimum_duration_days)
    if now < earliest_submission:
        raise DomainError(
            f"This contract requires {contract.minimum_duration_days} full days of testing",
            409,
        )
    session_count = db.scalar(
        select(func.count(TestingSession.id)).where(TestingSession.assignment_id == assignment_id)
    )
    if session_count is None or session_count < contract.required_sessions:
        raise DomainError(
            f"Record {contract.required_sessions} testing sessions before submitting",
            409,
        )
    valid_task_ids = {task.id for task in tasks}
    required_task_ids = {task.id for task in tasks if task.evidence_required}
    submitted_task_ids = {item.task_id for item in payload.items if item.task_id is not None}
    if not submitted_task_ids.issubset(valid_task_ids):
        raise DomainError("Evidence references a task outside this testing contract", 422)
    missing_tasks = required_task_ids - submitted_task_ids
    if missing_tasks:
        raise DomainError("Provide evidence for every required contract task", 422)
    for item in payload.items:
        if item.storage_key is None and item.external_url is None and item.note is None:
            raise DomainError("Every evidence item must include a file, link, or note", 422)
        if item.storage_key is not None:
            _validate_storage_key(assignment_id, item.storage_key)

    latest_version = db.scalar(
        select(func.max(EvidenceSubmission.version)).where(
            EvidenceSubmission.assignment_id == assignment_id
        )
    )
    submission = EvidenceSubmission(
        assignment_id=assignment_id,
        version=(latest_version or 0) + 1,
        summary=payload.summary,
    )
    db.add(submission)
    db.flush()
    for item in payload.items:
        item_values = item.model_dump()
        if item_values["external_url"] is not None:
            item_values["external_url"] = str(item_values["external_url"])
        db.add(
            EvidenceItem(
                submission_id=submission.id,
                **item_values,
            )
        )

    now = datetime.now(UTC)
    assignment.status = AssignmentStatus.SUBMITTED
    assignment.submitted_at = now
    add_audit_event(
        db,
        actor_id=tester_id,
        action="submission.created",
        entity_type="assignment",
        entity_id=assignment.id,
        details={"submission_id": str(submission.id), "version": submission.version},
    )
    campaign = get_campaign(db, assignment.campaign_id)
    add_notification(
        db,
        user_id=campaign.owner_id,
        kind="submission_received",
        title=f"Evidence ready for {campaign.name}",
        body=(
            f"{assignment.tester_profile.display_name} submitted version "
            f"{submission.version} for review."
        ),
        entity_type="submission",
        entity_id=submission.id,
        idempotency_key=f"submission:{submission.id}:owner-notification",
    )
    db.flush()
    return submission


def _validate_storage_key(assignment_id: UUID, storage_key: str) -> None:
    """Keep uploaded evidence inside the assignment folder enforced by Storage RLS."""
    path = PurePosixPath(storage_key)
    expected_folder = str(assignment_id)
    if (
        "\\" in storage_key
        or storage_key.startswith("/")
        or "://" in storage_key
        or ".." in path.parts
        or len(path.parts) < 2
        or path.parts[0] != expected_folder
        or not path.name
    ):
        raise DomainError(
            f"Evidence storage keys must use {expected_folder}/<file-name>",
            422,
        )


def get_submission(
    db: Session, *, submission_id: UUID, user_id: UUID
) -> tuple[EvidenceSubmission, list[EvidenceItem]]:
    submission = db.get(EvidenceSubmission, submission_id)
    if submission is None:
        raise DomainError("Submission not found", 404)
    assignment = get_assignment(db, submission.assignment_id)
    require_assignment_participant(db, assignment, user_id)
    items = list(
        db.scalars(
            select(EvidenceItem)
            .where(EvidenceItem.submission_id == submission_id)
            .order_by(EvidenceItem.created_at, EvidenceItem.id)
        )
    )
    return submission, items


def list_submissions(
    db: Session, *, assignment_id: UUID, user_id: UUID
) -> list[EvidenceSubmission]:
    assignment = get_assignment(db, assignment_id)
    require_assignment_participant(db, assignment, user_id)
    return list(
        db.scalars(
            select(EvidenceSubmission)
            .where(EvidenceSubmission.assignment_id == assignment_id)
            .order_by(EvidenceSubmission.version.desc())
        )
    )


def list_submission_reviews(db: Session, *, submission_id: UUID, user_id: UUID) -> list[Review]:
    submission, _ = get_submission(db, submission_id=submission_id, user_id=user_id)
    return list(
        db.scalars(
            select(Review)
            .where(Review.submission_id == submission.id)
            .order_by(Review.created_at, Review.id)
        )
    )


def create_review(
    db: Session, *, submission_id: UUID, reviewer_id: UUID, payload: ReviewCreate
) -> Review:
    submission = db.scalar(
        select(EvidenceSubmission).where(EvidenceSubmission.id == submission_id).with_for_update()
    )
    if submission is None:
        raise DomainError("Submission not found", 404)
    assignment = get_assignment(db, submission.assignment_id)
    campaign = db.scalar(
        select(Campaign).where(Campaign.id == assignment.campaign_id).with_for_update()
    )
    if campaign is None:
        raise DomainError("Campaign not found", 404)
    require_campaign_owner(campaign, reviewer_id)
    if submission.status != SubmissionStatus.SUBMITTED:
        raise DomainError("This submission has already been reviewed", 409)
    if assignment.status != AssignmentStatus.SUBMITTED:
        raise DomainError("The assignment is not awaiting review", 409)

    review = Review(
        submission_id=submission_id,
        reviewer_id=reviewer_id,
        decision=payload.decision,
        notes=payload.notes,
    )
    db.add(review)
    db.flush()

    action = f"submission.{payload.decision.value}"
    if payload.decision == ReviewDecision.APPROVED:
        submission.status = SubmissionStatus.APPROVED
        assignment.status = AssignmentStatus.APPROVED
        assignment.completed_at = datetime.now(UTC)
        record_credit_entry(
            db,
            user_id=assignment.tester_id,
            delta=campaign.reward_credits,
            entry_type=CreditEntryType.REWARD,
            idempotency_key=f"assignment:{assignment.id}:reward",
            reference_type="assignment",
            reference_id=assignment.id,
            note=f"Approved test for {campaign.name}",
            created_by=reviewer_id,
        )
        db.flush()
        maybe_complete_campaign(db, campaign=campaign, actor_id=reviewer_id)
    elif payload.decision == ReviewDecision.CHANGES_REQUESTED:
        submission.status = SubmissionStatus.CHANGES_REQUESTED
        assignment.status = AssignmentStatus.CHANGES_REQUESTED
    else:
        submission.status = SubmissionStatus.REJECTED
        assignment.status = AssignmentStatus.REJECTED

    add_audit_event(
        db,
        actor_id=reviewer_id,
        action=action,
        entity_type="assignment",
        entity_id=assignment.id,
        details={"submission_id": str(submission.id), "review_id": str(review.id)},
    )
    add_notification(
        db,
        user_id=assignment.tester_id,
        kind="submission_reviewed",
        title=f"Your {campaign.name} submission was {payload.decision.value.replace('_', ' ')}",
        body=payload.notes[:500],
        entity_type="submission",
        entity_id=submission.id,
        idempotency_key=f"review:{review.id}:tester-notification",
    )
    db.flush()
    return review


def add_message(
    db: Session, *, assignment_id: UUID, sender_id: UUID, payload: MessageCreate
) -> Message:
    assignment = get_assignment(db, assignment_id)
    require_assignment_participant(db, assignment, sender_id)
    message = Message(assignment_id=assignment_id, sender_id=sender_id, body=payload.body)
    db.add(message)
    db.flush()
    add_audit_event(
        db,
        actor_id=sender_id,
        action="message.created",
        entity_type="assignment",
        entity_id=assignment.id,
        details={"message_id": str(message.id)},
    )
    campaign = get_campaign(db, assignment.campaign_id)
    recipient_id = campaign.owner_id if sender_id == assignment.tester_id else assignment.tester_id
    add_notification(
        db,
        user_id=recipient_id,
        kind="message_received",
        title=f"New private message about {campaign.name}",
        body=payload.body[:500],
        entity_type="assignment",
        entity_id=assignment.id,
        idempotency_key=f"message:{message.id}:recipient-notification",
    )
    return message


def list_messages(db: Session, *, assignment_id: UUID, user_id: UUID) -> list[Message]:
    assignment = get_assignment(db, assignment_id)
    require_assignment_participant(db, assignment, user_id)
    return list(
        db.scalars(
            select(Message)
            .where(Message.assignment_id == assignment_id)
            .order_by(Message.created_at, Message.id)
        )
    )


def open_dispute(
    db: Session, *, assignment_id: UUID, opened_by: UUID, payload: DisputeCreate
) -> Dispute:
    assignment = db.scalar(
        select(Assignment).where(Assignment.id == assignment_id).with_for_update()
    )
    if assignment is None:
        raise DomainError("Assignment not found", 404)
    require_assignment_participant(db, assignment, opened_by)
    if assignment.tester_id != opened_by:
        raise DomainError("Only the tester can dispute a rejected submission", 403)
    if assignment.status != AssignmentStatus.REJECTED:
        raise DomainError("Only a rejected assignment can be disputed", 409)
    if payload.submission_id is None:
        raise DomainError("A rejected submission is required", 422)
    submission = db.get(EvidenceSubmission, payload.submission_id)
    if submission is None or submission.assignment_id != assignment_id:
        raise DomainError("The submission does not belong to this assignment", 422)
    if submission.status != SubmissionStatus.REJECTED:
        raise DomainError("Only a rejected submission can be disputed", 409)
    existing = db.scalar(select(Dispute.id).where(Dispute.assignment_id == assignment_id))
    if existing is not None:
        raise DomainError("This assignment already has a dispute", 409)
    dispute = Dispute(
        assignment_id=assignment_id,
        submission_id=payload.submission_id,
        opened_by=opened_by,
        reason=payload.reason,
    )
    db.add(dispute)
    db.flush()
    add_audit_event(
        db,
        actor_id=opened_by,
        action="dispute.opened",
        entity_type="assignment",
        entity_id=assignment.id,
        details={"dispute_id": str(dispute.id)},
    )
    campaign = get_campaign(db, assignment.campaign_id)
    add_notification(
        db,
        user_id=campaign.owner_id,
        kind="dispute_opened",
        title=f"A dispute was opened for {campaign.name}",
        body=payload.reason[:500],
        entity_type="dispute",
        entity_id=dispute.id,
        idempotency_key=f"dispute:{dispute.id}:owner-notification",
    )
    return dispute


def list_user_disputes(db: Session, user_id: UUID) -> list[Dispute]:
    return list(
        db.scalars(
            select(Dispute)
            .join(Assignment, Assignment.id == Dispute.assignment_id)
            .join(Campaign, Campaign.id == Assignment.campaign_id)
            .where(or_(Assignment.tester_id == user_id, Campaign.owner_id == user_id))
            .order_by(Dispute.created_at.desc())
        )
    )
