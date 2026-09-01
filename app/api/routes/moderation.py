from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query

from app.api.deps import DBSession
from app.core.auth import ModeratorUser
from app.models import Dispute, Profile, WaitlistEntry
from app.models.enums import DisputeStatus
from app.schemas.api import (
    AssignmentRead,
    AuditEventRead,
    CampaignRead,
    ContractRead,
    ContractTaskRead,
    DisputeRead,
    DisputeResolve,
    EvidenceItemRead,
    MessageRead,
    ModerationDisputeCaseRead,
    ModerationParticipantRead,
    ParticipantSuspension,
    ReviewRead,
    SubmissionRead,
    WaitlistRead,
)
from app.services.moderation import (
    ModerationCase,
    claim_dispute,
    get_moderation_case,
    list_disputes,
    resolve_dispute,
)
from app.services.profiles import (
    list_participants,
    list_waitlist,
    restore_participant,
    suspend_participant,
)

router = APIRouter(prefix="/moderation", tags=["moderation"])


def case_response(case: ModerationCase) -> ModerationDisputeCaseRead:
    contract = ContractRead(
        id=case.contract.id,
        campaign_id=case.contract.campaign_id,
        version=case.contract.version,
        tester_instructions=case.contract.tester_instructions,
        access_instructions=case.contract.access_instructions,
        device_requirements=case.contract.device_requirements,
        evidence_requirements=case.contract.evidence_requirements,
        review_window_hours=case.contract.review_window_hours,
        minimum_duration_days=case.contract.minimum_duration_days,
        required_sessions=case.contract.required_sessions,
        status=case.contract.status,
        locked_at=case.contract.locked_at,
        created_at=case.contract.created_at,
        updated_at=case.contract.updated_at,
        tasks=[ContractTaskRead.model_validate(task) for task in case.tasks],
    )
    submissions = [
        SubmissionRead(
            id=submission.id,
            assignment_id=submission.assignment_id,
            version=submission.version,
            summary=submission.summary,
            status=submission.status,
            submitted_at=submission.submitted_at,
            items=[
                EvidenceItemRead.model_validate(item)
                for item in case.evidence_by_submission[submission.id]
            ],
        )
        for submission in case.submissions
    ]
    return ModerationDisputeCaseRead(
        dispute=DisputeRead.model_validate(case.dispute),
        assignment=AssignmentRead.model_validate(case.assignment),
        campaign=CampaignRead.model_validate(case.campaign),
        contract=contract,
        submissions=submissions,
        reviews=[ReviewRead.model_validate(review) for review in case.reviews],
        messages=[MessageRead.model_validate(message) for message in case.messages],
        audit_events=[AuditEventRead.model_validate(event) for event in case.audit_events],
    )


@router.get("/disputes", response_model=list[DisputeRead])
def disputes(
    _: ModeratorUser,
    db: DBSession,
    status_filter: Annotated[DisputeStatus | None, Query(alias="status")] = None,
) -> list[Dispute]:
    return list_disputes(db, status_filter)


@router.get("/disputes/{dispute_id}", response_model=ModerationDisputeCaseRead)
def dispute_case(dispute_id: UUID, _: ModeratorUser, db: DBSession) -> ModerationDisputeCaseRead:
    return case_response(get_moderation_case(db, dispute_id))


@router.post("/disputes/{dispute_id}/claim", response_model=DisputeRead)
def claim(dispute_id: UUID, user: ModeratorUser, db: DBSession) -> Dispute:
    return claim_dispute(db, dispute_id=dispute_id, moderator_id=user.id)


@router.post("/disputes/{dispute_id}/resolve", response_model=DisputeRead)
def resolve(
    dispute_id: UUID,
    payload: DisputeResolve,
    user: ModeratorUser,
    db: DBSession,
) -> Dispute:
    return resolve_dispute(
        db,
        dispute_id=dispute_id,
        moderator_id=user.id,
        payload=payload,
    )


@router.get("/participants", response_model=list[ModerationParticipantRead])
def participants(_: ModeratorUser, db: DBSession) -> list[Profile]:
    return list_participants(db)


@router.post(
    "/participants/{participant_id}/suspend",
    response_model=ModerationParticipantRead,
)
def suspend(
    participant_id: UUID,
    payload: ParticipantSuspension,
    user: ModeratorUser,
    db: DBSession,
) -> Profile:
    return suspend_participant(
        db,
        participant_id=participant_id,
        moderator_id=user.id,
        reason=payload.reason,
    )


@router.post(
    "/participants/{participant_id}/restore",
    response_model=ModerationParticipantRead,
)
def restore(participant_id: UUID, user: ModeratorUser, db: DBSession) -> Profile:
    return restore_participant(db, participant_id=participant_id, moderator_id=user.id)


@router.get("/waitlist", response_model=list[WaitlistRead])
def waitlist(_: ModeratorUser, db: DBSession) -> list[WaitlistEntry]:
    return list_waitlist(db)
