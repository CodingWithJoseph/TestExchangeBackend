from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.models import Notification
from app.services.common import DomainError


def add_notification(
    db: Session,
    *,
    user_id: UUID,
    kind: str,
    title: str,
    body: str,
    entity_type: str,
    entity_id: UUID,
    idempotency_key: str,
) -> Notification:
    existing = db.scalar(
        select(Notification).where(Notification.idempotency_key == idempotency_key)
    )
    if existing is not None:
        return existing
    notification = Notification(
        user_id=user_id,
        kind=kind,
        title=title,
        body=body,
        entity_type=entity_type,
        entity_id=entity_id,
        idempotency_key=idempotency_key,
    )
    db.add(notification)
    db.flush()
    return notification


def list_notifications(db: Session, user_id: UUID) -> list[Notification]:
    return list(
        db.scalars(
            select(Notification)
            .where(Notification.user_id == user_id)
            .order_by(Notification.created_at.desc(), Notification.id.desc())
            .limit(100)
        )
    )


def mark_notification_read(db: Session, *, notification_id: UUID, user_id: UUID) -> Notification:
    notification = db.scalar(
        select(Notification).where(Notification.id == notification_id).with_for_update()
    )
    if notification is None:
        raise DomainError("Notification not found", 404)
    if notification.user_id != user_id:
        raise DomainError("Notification not found", 404)
    if notification.read_at is None:
        notification.read_at = datetime.now(UTC)
        db.flush()
    return notification


def mark_all_notifications_read(db: Session, user_id: UUID) -> None:
    db.execute(
        update(Notification)
        .where(Notification.user_id == user_id, Notification.read_at.is_(None))
        .values(read_at=datetime.now(UTC))
    )
