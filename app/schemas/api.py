from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, HttpUrl

from app.models.enums import (
    AssignmentStatus,
    CampaignStatus,
    ContractStatus,
    CreditEntryType,
    DisputeStatus,
    EvidenceKind,
    Platform,
    ReviewDecision,
    SubmissionStatus,
)


class APIModel(BaseModel):
    model_config = ConfigDict(from_attributes=True, str_strip_whitespace=True)


class HealthRead(APIModel):
    status: str
    environment: str


class ProfileUpsert(APIModel):
    username: str = Field(min_length=3, max_length=40, pattern=r"^[a-zA-Z0-9_-]+$")
    display_name: str = Field(min_length=1, max_length=100)
    bio: str | None = Field(default=None, max_length=500)
    avatar_url: HttpUrl | None = None


class ProfileRead(APIModel):
    id: UUID
    email: str | None
    username: str
    display_name: str
    bio: str | None
    avatar_url: str | None
    created_at: datetime
    updated_at: datetime


class CampaignCreate(APIModel):
    name: str = Field(min_length=2, max_length=120)
    slug: str = Field(min_length=3, max_length=140, pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
    platform: Platform
    category: str = Field(min_length=2, max_length=80)
    public_summary: str = Field(min_length=20, max_length=800)
    public_tester_requirements: str = Field(min_length=10, max_length=800)
    minimum_version: str | None = Field(default=None, max_length=80)
    target_testers: int = Field(ge=1, le=100)
    reward_credits: int = Field(ge=1, le=1000)


class CampaignUpdate(APIModel):
    name: str | None = Field(default=None, min_length=2, max_length=120)
    category: str | None = Field(default=None, min_length=2, max_length=80)
    public_summary: str | None = Field(default=None, min_length=20, max_length=800)
    public_tester_requirements: str | None = Field(default=None, min_length=10, max_length=800)
    minimum_version: str | None = Field(default=None, max_length=80)
    target_testers: int | None = Field(default=None, ge=1, le=100)
    reward_credits: int | None = Field(default=None, ge=1, le=1000)


class CampaignRead(APIModel):
    id: UUID
    owner_id: UUID
    name: str
    slug: str
    platform: Platform
    category: str
    public_summary: str
    public_tester_requirements: str
    minimum_version: str | None
    target_testers: int
    reward_credits: int
    status: CampaignStatus
    published_at: datetime | None
    created_at: datetime
    updated_at: datetime


class ContractTaskInput(APIModel):
    title: str = Field(min_length=2, max_length=160)
    instructions: str = Field(min_length=10, max_length=4000)
    evidence_required: bool = True


class ContractTaskRead(APIModel):
    id: UUID
    position: int
    title: str
    instructions: str
    evidence_required: bool


class ContractUpsert(APIModel):
    tester_instructions: str = Field(min_length=20, max_length=8000)
    access_instructions: str | None = Field(default=None, max_length=4000)
    device_requirements: str | None = Field(default=None, max_length=4000)
    evidence_requirements: str = Field(min_length=10, max_length=4000)
    review_window_hours: int = Field(default=72, ge=1, le=720)
    tasks: list[ContractTaskInput] = Field(min_length=1, max_length=25)


class ContractRead(APIModel):
    id: UUID
    campaign_id: UUID
    version: int
    tester_instructions: str
    access_instructions: str | None
    device_requirements: str | None
    evidence_requirements: str
    review_window_hours: int
    status: ContractStatus
    locked_at: datetime | None
    tasks: list[ContractTaskRead]


class AssignmentApply(APIModel):
    application_note: str | None = Field(default=None, max_length=1000)


class AssignmentRead(APIModel):
    id: UUID
    campaign_id: UUID
    tester_id: UUID
    application_note: str | None
    status: AssignmentStatus
    accepted_at: datetime | None
    started_at: datetime | None
    submitted_at: datetime | None
    completed_at: datetime | None
    created_at: datetime
    updated_at: datetime


class EvidenceItemCreate(APIModel):
    task_id: UUID | None = None
    kind: EvidenceKind
    storage_key: str | None = Field(default=None, max_length=500)
    external_url: HttpUrl | None = None
    note: str | None = Field(default=None, max_length=8000)


class SubmissionCreate(APIModel):
    summary: str = Field(min_length=20, max_length=8000)
    items: list[EvidenceItemCreate] = Field(min_length=1, max_length=50)


class EvidenceItemRead(APIModel):
    id: UUID
    task_id: UUID | None
    kind: EvidenceKind
    storage_key: str | None
    external_url: str | None
    note: str | None
    created_at: datetime


class SubmissionRead(APIModel):
    id: UUID
    assignment_id: UUID
    version: int
    summary: str
    status: SubmissionStatus
    submitted_at: datetime
    items: list[EvidenceItemRead]


class QualityCheckItem(APIModel):
    code: str
    label: str
    status: Literal["passed", "flagged"]
    detail: str


class QualityCheckRead(APIModel):
    submission_id: UUID
    assignment_id: UUID
    submission_version: int
    submission_status: SubmissionStatus
    status: Literal["ready_for_review", "needs_attention", "already_reviewed"]
    score: int = Field(ge=0, le=100)
    checks: list[QualityCheckItem]
    disclaimer: str


class MessageCreate(APIModel):
    body: str = Field(min_length=1, max_length=4000)


class MessageRead(APIModel):
    id: UUID
    assignment_id: UUID
    sender_id: UUID
    body: str
    created_at: datetime


class ReviewCreate(APIModel):
    decision: ReviewDecision
    notes: str = Field(min_length=5, max_length=8000)


class ReviewRead(APIModel):
    id: UUID
    submission_id: UUID
    reviewer_id: UUID
    decision: ReviewDecision
    notes: str
    created_at: datetime


class CreditBalanceRead(APIModel):
    user_id: UUID
    balance: int


class CreditLedgerEntryRead(APIModel):
    id: UUID
    transaction_id: UUID
    user_id: UUID
    delta: int
    entry_type: CreditEntryType
    reference_type: str | None
    reference_id: UUID | None
    note: str | None
    idempotency_key: str
    created_by: UUID | None
    created_at: datetime


class DisputeCreate(APIModel):
    submission_id: UUID | None = None
    reason: str = Field(min_length=20, max_length=8000)


class DisputeRead(APIModel):
    id: UUID
    assignment_id: UUID
    submission_id: UUID | None
    opened_by: UUID
    reason: str
    status: DisputeStatus
    assigned_to: UUID | None
    assigned_at: datetime | None
    resolution: str | None
    resolved_by: UUID | None
    resolved_at: datetime | None
    created_at: datetime
    updated_at: datetime


class DisputeResolve(APIModel):
    outcome: Literal["resolved", "rejected"]
    resolution: str = Field(min_length=20, max_length=8000)


class AuditEventRead(APIModel):
    id: UUID
    actor_id: UUID | None
    action: str
    entity_type: str
    entity_id: UUID
    details: dict
    created_at: datetime


class ModerationDisputeCaseRead(APIModel):
    dispute: DisputeRead
    assignment: AssignmentRead
    campaign: CampaignRead
    contract: ContractRead
    submissions: list[SubmissionRead]
    reviews: list[ReviewRead]
    messages: list[MessageRead]
    audit_events: list[AuditEventRead]
