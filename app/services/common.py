from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Assignment, AuditEvent, Campaign, Profile


class DomainError(Exception):
    def __init__(self, detail: str, status_code: int = 400) -> None:
        super().__init__(detail)
        self.detail = detail
        self.status_code = status_code


def get_profile(db: Session, user_id: UUID) -> Profile:
    profile = db.get(Profile, user_id)
    if profile is None:
        raise DomainError("Create your profile before using this feature", 409)
    return profile


def get_campaign(db: Session, campaign_id: UUID) -> Campaign:
    campaign = db.get(Campaign, campaign_id)
    if campaign is None:
        raise DomainError("Campaign not found", 404)
    return campaign


def get_assignment(db: Session, assignment_id: UUID) -> Assignment:
    assignment = db.get(Assignment, assignment_id)
    if assignment is None:
        raise DomainError("Assignment not found", 404)
    return assignment


def require_campaign_owner(campaign: Campaign, user_id: UUID) -> None:
    if campaign.owner_id != user_id:
        raise DomainError("Only the campaign owner can perform this action", 403)


def require_assignment_participant(db: Session, assignment: Assignment, user_id: UUID) -> Campaign:
    campaign = get_campaign(db, assignment.campaign_id)
    if user_id not in {assignment.tester_id, campaign.owner_id}:
        raise DomainError("This workspace is private to its tester and campaign owner", 403)
    return campaign


def add_audit_event(
    db: Session,
    *,
    actor_id: UUID | None,
    action: str,
    entity_type: str,
    entity_id: UUID,
    details: dict | None = None,
) -> AuditEvent:
    event = AuditEvent(
        actor_id=actor_id,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        details=details or {},
    )
    db.add(event)
    return event


def list_assignment_audit(db: Session, assignment_id: UUID) -> list[AuditEvent]:
    return list(
        db.scalars(
            select(AuditEvent)
            .where(AuditEvent.entity_type == "assignment", AuditEvent.entity_id == assignment_id)
            .order_by(AuditEvent.created_at, AuditEvent.id)
        )
    )
