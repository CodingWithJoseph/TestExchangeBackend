from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    event,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
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


def enum_column(enum_type: type, name: str) -> Enum:
    return Enum(
        enum_type,
        name=name,
        native_enum=False,
        values_callable=lambda members: [member.value for member in members],
    )


class Profile(Base, TimestampMixin):
    __tablename__ = "profiles"

    # This UUID is the Supabase auth.users.id claim (`sub`).
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    email: Mapped[str | None] = mapped_column(String(320))
    username: Mapped[str] = mapped_column(String(40), unique=True, index=True)
    display_name: Mapped[str] = mapped_column(String(100))
    bio: Mapped[str | None] = mapped_column(String(500))
    avatar_url: Mapped[str | None] = mapped_column(String(500))


class Campaign(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "campaigns"
    __table_args__ = (
        CheckConstraint("target_testers > 0", name="ck_campaign_target_testers_positive"),
        CheckConstraint("reward_credits > 0", name="ck_campaign_reward_positive"),
        Index("ix_campaigns_status_created", "status", "created_at"),
    )

    owner_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("profiles.id", ondelete="RESTRICT"), index=True
    )
    name: Mapped[str] = mapped_column(String(120))
    slug: Mapped[str] = mapped_column(String(140), unique=True, index=True)
    platform: Mapped[Platform] = mapped_column(enum_column(Platform, "platform"))
    category: Mapped[str] = mapped_column(String(80))
    public_summary: Mapped[str] = mapped_column(String(800))
    public_tester_requirements: Mapped[str] = mapped_column(String(800))
    minimum_version: Mapped[str | None] = mapped_column(String(80))
    target_testers: Mapped[int] = mapped_column(Integer)
    reward_credits: Mapped[int] = mapped_column(Integer)
    status: Mapped[CampaignStatus] = mapped_column(
        enum_column(CampaignStatus, "campaign_status"), default=CampaignStatus.DRAFT, index=True
    )
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class TestingContract(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "testing_contracts"

    campaign_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("campaigns.id", ondelete="CASCADE"), unique=True, index=True
    )
    version: Mapped[int] = mapped_column(Integer, default=1)
    tester_instructions: Mapped[str] = mapped_column(Text)
    device_requirements: Mapped[str | None] = mapped_column(Text)
    evidence_requirements: Mapped[str] = mapped_column(Text)
    review_window_hours: Mapped[int] = mapped_column(Integer, default=72)
    status: Mapped[ContractStatus] = mapped_column(
        enum_column(ContractStatus, "contract_status"), default=ContractStatus.DRAFT
    )
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ContractTask(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "contract_tasks"
    __table_args__ = (
        UniqueConstraint("contract_id", "position", name="uq_contract_task_position"),
        CheckConstraint("position >= 0", name="ck_contract_task_position_nonnegative"),
    )

    contract_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("testing_contracts.id", ondelete="CASCADE"), index=True
    )
    position: Mapped[int] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(String(160))
    instructions: Mapped[str] = mapped_column(Text)
    evidence_required: Mapped[bool] = mapped_column(Boolean, default=True)


class Assignment(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "assignments"
    __table_args__ = (
        UniqueConstraint("campaign_id", "tester_id", name="uq_campaign_tester"),
        Index("ix_assignments_tester_status", "tester_id", "status"),
    )

    campaign_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("campaigns.id", ondelete="RESTRICT"), index=True
    )
    tester_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("profiles.id", ondelete="RESTRICT"), index=True
    )
    application_note: Mapped[str | None] = mapped_column(String(1000))
    status: Mapped[AssignmentStatus] = mapped_column(
        enum_column(AssignmentStatus, "assignment_status"),
        default=AssignmentStatus.APPLIED,
        index=True,
    )
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class EvidenceSubmission(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "evidence_submissions"
    __table_args__ = (
        UniqueConstraint("assignment_id", "version", name="uq_assignment_submission_version"),
        CheckConstraint("version > 0", name="ck_submission_version_positive"),
    )

    assignment_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("assignments.id", ondelete="RESTRICT"), index=True
    )
    version: Mapped[int] = mapped_column(Integer)
    summary: Mapped[str] = mapped_column(Text)
    status: Mapped[SubmissionStatus] = mapped_column(
        enum_column(SubmissionStatus, "submission_status"),
        default=SubmissionStatus.SUBMITTED,
        index=True,
    )
    submitted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class EvidenceItem(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "evidence_items"
    __table_args__ = (
        CheckConstraint(
            "storage_key IS NOT NULL OR external_url IS NOT NULL OR note IS NOT NULL",
            name="ck_evidence_has_content",
        ),
    )

    submission_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("evidence_submissions.id", ondelete="CASCADE"), index=True
    )
    task_id: Mapped[UUID | None] = mapped_column(
        Uuid, ForeignKey("contract_tasks.id", ondelete="SET NULL"), index=True
    )
    kind: Mapped[EvidenceKind] = mapped_column(enum_column(EvidenceKind, "evidence_kind"))
    storage_key: Mapped[str | None] = mapped_column(String(500))
    external_url: Mapped[str | None] = mapped_column(String(1000))
    note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        server_default=func.now(),
        nullable=False,
    )


class Message(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "messages"
    __table_args__ = (Index("ix_messages_assignment_created", "assignment_id", "created_at"),)

    assignment_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("assignments.id", ondelete="CASCADE")
    )
    sender_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("profiles.id", ondelete="RESTRICT"), index=True
    )
    body: Mapped[str] = mapped_column(String(4000))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class Review(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "reviews"

    submission_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("evidence_submissions.id", ondelete="RESTRICT"), unique=True, index=True
    )
    reviewer_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("profiles.id", ondelete="RESTRICT"), index=True
    )
    decision: Mapped[ReviewDecision] = mapped_column(enum_column(ReviewDecision, "review_decision"))
    notes: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class CreditAccount(Base):
    __tablename__ = "credit_accounts"
    __table_args__ = (CheckConstraint("balance >= 0", name="ck_credit_balance_nonnegative"),)

    user_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("profiles.id", ondelete="RESTRICT"), primary_key=True
    )
    balance: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class CreditLedgerEntry(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "credit_ledger_entries"
    __table_args__ = (
        CheckConstraint("delta != 0", name="ck_credit_delta_nonzero"),
        Index("ix_credit_ledger_user_created", "user_id", "created_at"),
    )

    transaction_id: Mapped[UUID] = mapped_column(Uuid, default=uuid4, index=True)
    user_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("profiles.id", ondelete="RESTRICT"), index=True
    )
    delta: Mapped[int] = mapped_column(Integer)
    entry_type: Mapped[CreditEntryType] = mapped_column(
        enum_column(CreditEntryType, "credit_entry_type")
    )
    reference_type: Mapped[str | None] = mapped_column(String(80))
    reference_id: Mapped[UUID | None] = mapped_column(Uuid)
    note: Mapped[str | None] = mapped_column(String(500))
    idempotency_key: Mapped[str] = mapped_column(String(200), unique=True)
    created_by: Mapped[UUID | None] = mapped_column(
        Uuid, ForeignKey("profiles.id", ondelete="RESTRICT")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        server_default=func.now(),
        nullable=False,
    )


class Dispute(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "disputes"
    __table_args__ = (Index("ix_disputes_status_created", "status", "created_at"),)

    assignment_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("assignments.id", ondelete="RESTRICT"), index=True
    )
    submission_id: Mapped[UUID | None] = mapped_column(
        Uuid, ForeignKey("evidence_submissions.id", ondelete="RESTRICT")
    )
    opened_by: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("profiles.id", ondelete="RESTRICT"), index=True
    )
    reason: Mapped[str] = mapped_column(Text)
    status: Mapped[DisputeStatus] = mapped_column(
        enum_column(DisputeStatus, "dispute_status"), default=DisputeStatus.OPEN, index=True
    )
    resolution: Mapped[str | None] = mapped_column(Text)
    resolved_by: Mapped[UUID | None] = mapped_column(
        Uuid, ForeignKey("profiles.id", ondelete="RESTRICT")
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AuditEvent(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "audit_events"
    __table_args__ = (Index("ix_audit_entity_created", "entity_type", "entity_id", "created_at"),)

    actor_id: Mapped[UUID | None] = mapped_column(
        Uuid, ForeignKey("profiles.id", ondelete="RESTRICT"), index=True
    )
    action: Mapped[str] = mapped_column(String(120), index=True)
    entity_type: Mapped[str] = mapped_column(String(80))
    entity_id: Mapped[UUID] = mapped_column(Uuid)
    details: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class ImmutableLedgerError(RuntimeError):
    pass


@event.listens_for(CreditLedgerEntry, "before_update")
@event.listens_for(CreditLedgerEntry, "before_delete")
def prevent_ledger_mutation(*_: object) -> None:
    raise ImmutableLedgerError("Credit ledger entries are append-only")
