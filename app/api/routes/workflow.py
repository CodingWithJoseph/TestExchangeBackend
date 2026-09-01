from uuid import UUID

from fastapi import APIRouter, status

from app.api.deps import DBSession
from app.api.routes.campaigns import contract_response
from app.core.auth import AuthenticatedUser
from app.models import Assignment, Campaign, Dispute, Message, Review, TestingSession
from app.models.enums import AssignmentStatus
from app.schemas.api import (
    AssignmentApply,
    AssignmentRead,
    AuditEventRead,
    CampaignRead,
    ContractRead,
    DisputeCreate,
    DisputeRead,
    EvidenceItemRead,
    MessageCreate,
    MessageRead,
    QualityCheckRead,
    ReviewCreate,
    ReviewRead,
    SubmissionCreate,
    SubmissionRead,
    TestingSessionCreate,
    TestingSessionRead,
)
from app.services.common import (
    DomainError,
    get_assignment,
    list_assignment_audit,
    require_assignment_participant,
)
from app.services.quality import build_submission_quality_check
from app.services.workflow import (
    accept_assignment,
    add_message,
    apply_to_campaign,
    create_review,
    create_submission,
    decline_assignment,
    get_submission,
    list_messages,
    list_submission_reviews,
    list_submissions,
    list_testing_sessions,
    list_user_assignments,
    list_user_disputes,
    open_dispute,
    record_testing_session,
    start_assignment,
    withdraw_assignment,
)

router = APIRouter(tags=["testing workflow"])


def submission_response(db: DBSession, submission_id: UUID, user_id: UUID) -> SubmissionRead:
    submission, items = get_submission(db, submission_id=submission_id, user_id=user_id)
    return SubmissionRead(
        id=submission.id,
        assignment_id=submission.assignment_id,
        version=submission.version,
        summary=submission.summary,
        status=submission.status,
        submitted_at=submission.submitted_at,
        items=[EvidenceItemRead.model_validate(item) for item in items],
    )


@router.post(
    "/campaigns/{campaign_id}/assignments",
    response_model=AssignmentRead,
    status_code=status.HTTP_201_CREATED,
)
def apply(
    campaign_id: UUID,
    payload: AssignmentApply,
    user: AuthenticatedUser,
    db: DBSession,
) -> Assignment:
    return apply_to_campaign(db, campaign_id=campaign_id, tester_id=user.id, payload=payload)


@router.get("/assignments/mine", response_model=list[AssignmentRead])
def assignments(user: AuthenticatedUser, db: DBSession) -> list[Assignment]:
    return list_user_assignments(db, user.id)


@router.get("/assignments/{assignment_id}", response_model=AssignmentRead)
def assignment(assignment_id: UUID, user: AuthenticatedUser, db: DBSession) -> Assignment:
    record = get_assignment(db, assignment_id)
    require_assignment_participant(db, record, user.id)
    return record


@router.get("/assignments/{assignment_id}/campaign", response_model=CampaignRead)
def assignment_campaign(assignment_id: UUID, user: AuthenticatedUser, db: DBSession) -> Campaign:
    record = get_assignment(db, assignment_id)
    require_assignment_participant(db, record, user.id)
    campaign = db.get(Campaign, record.campaign_id)
    if campaign is None:
        raise DomainError("Campaign not found", 404)
    return campaign


@router.get("/assignments/{assignment_id}/contract", response_model=ContractRead)
def assignment_contract(
    assignment_id: UUID, user: AuthenticatedUser, db: DBSession
) -> ContractRead:
    record = get_assignment(db, assignment_id)
    require_assignment_participant(db, record, user.id)
    if user.id == record.tester_id and record.status == AssignmentStatus.APPLIED:
        raise DomainError("The private testing contract unlocks after acceptance", 403)
    return contract_response(db, record.campaign_id)


@router.post("/assignments/{assignment_id}/accept", response_model=AssignmentRead)
def accept(assignment_id: UUID, user: AuthenticatedUser, db: DBSession) -> Assignment:
    return accept_assignment(db, assignment_id=assignment_id, owner_id=user.id)


@router.post("/assignments/{assignment_id}/decline", response_model=AssignmentRead)
def decline(assignment_id: UUID, user: AuthenticatedUser, db: DBSession) -> Assignment:
    return decline_assignment(db, assignment_id=assignment_id, owner_id=user.id)


@router.post("/assignments/{assignment_id}/withdraw", response_model=AssignmentRead)
def withdraw(assignment_id: UUID, user: AuthenticatedUser, db: DBSession) -> Assignment:
    return withdraw_assignment(db, assignment_id=assignment_id, tester_id=user.id)


@router.post("/assignments/{assignment_id}/start", response_model=AssignmentRead)
def start(assignment_id: UUID, user: AuthenticatedUser, db: DBSession) -> Assignment:
    return start_assignment(db, assignment_id=assignment_id, tester_id=user.id)


@router.get(
    "/assignments/{assignment_id}/sessions",
    response_model=list[TestingSessionRead],
)
def testing_sessions(
    assignment_id: UUID, user: AuthenticatedUser, db: DBSession
) -> list[TestingSession]:
    return list_testing_sessions(db, assignment_id=assignment_id, user_id=user.id)


@router.post(
    "/assignments/{assignment_id}/sessions",
    response_model=TestingSessionRead,
    status_code=status.HTTP_201_CREATED,
)
def testing_session(
    assignment_id: UUID,
    payload: TestingSessionCreate,
    user: AuthenticatedUser,
    db: DBSession,
) -> TestingSession:
    return record_testing_session(
        db,
        assignment_id=assignment_id,
        tester_id=user.id,
        payload=payload,
    )


@router.post(
    "/assignments/{assignment_id}/submissions",
    response_model=SubmissionRead,
    status_code=status.HTTP_201_CREATED,
)
def submit(
    assignment_id: UUID,
    payload: SubmissionCreate,
    user: AuthenticatedUser,
    db: DBSession,
) -> SubmissionRead:
    submission = create_submission(
        db, assignment_id=assignment_id, tester_id=user.id, payload=payload
    )
    return submission_response(db, submission.id, user.id)


@router.get("/submissions/{submission_id}", response_model=SubmissionRead)
def submission(submission_id: UUID, user: AuthenticatedUser, db: DBSession) -> SubmissionRead:
    return submission_response(db, submission_id, user.id)


@router.get(
    "/submissions/{submission_id}/quality-check",
    response_model=QualityCheckRead,
    summary="Get an advisory quality check for a private submission",
)
def submission_quality_check(
    submission_id: UUID, user: AuthenticatedUser, db: DBSession
) -> QualityCheckRead:
    return build_submission_quality_check(db, submission_id=submission_id, user_id=user.id)


@router.get(
    "/assignments/{assignment_id}/submissions",
    response_model=list[SubmissionRead],
)
def submissions(
    assignment_id: UUID, user: AuthenticatedUser, db: DBSession
) -> list[SubmissionRead]:
    records = list_submissions(db, assignment_id=assignment_id, user_id=user.id)
    return [submission_response(db, record.id, user.id) for record in records]


@router.post("/submissions/{submission_id}/reviews", response_model=ReviewRead)
def review(
    submission_id: UUID,
    payload: ReviewCreate,
    user: AuthenticatedUser,
    db: DBSession,
) -> Review:
    return create_review(db, submission_id=submission_id, reviewer_id=user.id, payload=payload)


@router.get("/submissions/{submission_id}/reviews", response_model=list[ReviewRead])
def reviews(submission_id: UUID, user: AuthenticatedUser, db: DBSession) -> list[Review]:
    return list_submission_reviews(db, submission_id=submission_id, user_id=user.id)


@router.get("/assignments/{assignment_id}/messages", response_model=list[MessageRead])
def messages(assignment_id: UUID, user: AuthenticatedUser, db: DBSession) -> list[Message]:
    return list_messages(db, assignment_id=assignment_id, user_id=user.id)


@router.post(
    "/assignments/{assignment_id}/messages",
    response_model=MessageRead,
    status_code=status.HTTP_201_CREATED,
)
def message(
    assignment_id: UUID,
    payload: MessageCreate,
    user: AuthenticatedUser,
    db: DBSession,
) -> Message:
    return add_message(db, assignment_id=assignment_id, sender_id=user.id, payload=payload)


@router.post(
    "/assignments/{assignment_id}/disputes",
    response_model=DisputeRead,
    status_code=status.HTTP_201_CREATED,
)
def dispute(
    assignment_id: UUID,
    payload: DisputeCreate,
    user: AuthenticatedUser,
    db: DBSession,
) -> Dispute:
    return open_dispute(db, assignment_id=assignment_id, opened_by=user.id, payload=payload)


@router.get("/disputes/mine", response_model=list[DisputeRead])
def disputes(user: AuthenticatedUser, db: DBSession) -> list[Dispute]:
    return list_user_disputes(db, user.id)


@router.get("/assignments/{assignment_id}/audit", response_model=list[AuditEventRead])
def audit(assignment_id: UUID, user: AuthenticatedUser, db: DBSession) -> list:
    record = get_assignment(db, assignment_id)
    require_assignment_participant(db, record, user.id)
    return list_assignment_audit(db, assignment_id)
