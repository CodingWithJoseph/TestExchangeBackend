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


OCCUPIED_ASSIGNMENT_STATUSES = (
    AssignmentStatus.ACCEPTED,
    AssignmentStatus.IN_PROGRESS,
    AssignmentStatus.SUBMITTED,
    AssignmentStatus.CHANGES_REQUESTED,
    AssignmentStatus.APPROVED,
)

ACTIVE_TESTING_ASSIGNMENT_STATUSES = (
    AssignmentStatus.ACCEPTED,
    AssignmentStatus.IN_PROGRESS,
    AssignmentStatus.SUBMITTED,
    AssignmentStatus.CHANGES_REQUESTED,
)


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
    POSTING = "posting"
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


class DisputeRemedy(StrEnum):
    NONE = "none"
    AWARD_TESTER = "award_tester"
