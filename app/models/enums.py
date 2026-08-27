from enum import StrEnum


class Platform(StrEnum):
    ANDROID = "android"
    IOS = "ios"
    WEB = "web"
    DESKTOP = "desktop"
    API = "api"
    OTHER = "other"


class CampaignStatus(StrEnum):
    DRAFT = "draft"
    PUBLISHED = "published"
    PAUSED = "paused"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class ContractStatus(StrEnum):
    DRAFT = "draft"
    LOCKED = "locked"


class AssignmentStatus(StrEnum):
    APPLIED = "applied"
    ACCEPTED = "accepted"
    IN_PROGRESS = "in_progress"
    SUBMITTED = "submitted"
    CHANGES_REQUESTED = "changes_requested"
    APPROVED = "approved"
    REJECTED = "rejected"
    CANCELLED = "cancelled"


class SubmissionStatus(StrEnum):
    SUBMITTED = "submitted"
    CHANGES_REQUESTED = "changes_requested"
    APPROVED = "approved"
    REJECTED = "rejected"


class ReviewDecision(StrEnum):
    APPROVED = "approved"
    CHANGES_REQUESTED = "changes_requested"
    REJECTED = "rejected"


class EvidenceKind(StrEnum):
    SCREENSHOT = "screenshot"
    VIDEO = "video"
    LOG = "log"
    NOTE = "note"
    LINK = "link"
    FILE = "file"


class CreditEntryType(StrEnum):
    SIGNUP_GRANT = "signup_grant"
    PURCHASE = "purchase"
    RESERVATION = "reservation"
    REWARD = "reward"
    RELEASE = "release"
    REFUND = "refund"
    ADJUSTMENT = "adjustment"


class DisputeStatus(StrEnum):
    OPEN = "open"
    UNDER_REVIEW = "under_review"
    RESOLVED = "resolved"
    REJECTED = "rejected"
