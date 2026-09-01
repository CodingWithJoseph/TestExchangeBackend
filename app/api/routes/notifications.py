from uuid import UUID

from fastapi import APIRouter, Response, status

from app.api.deps import DBSession
from app.core.auth import AuthenticatedUser
from app.models import Notification
from app.schemas.api import NotificationRead
from app.services.notifications import (
    list_notifications,
    mark_all_notifications_read,
    mark_notification_read,
)

router = APIRouter(prefix="/notifications", tags=["notifications"])


@router.get("", response_model=list[NotificationRead])
def notifications(user: AuthenticatedUser, db: DBSession) -> list[Notification]:
    return list_notifications(db, user.id)


@router.post("/{notification_id}/read", response_model=NotificationRead)
def read_notification(
    notification_id: UUID, user: AuthenticatedUser, db: DBSession
) -> Notification:
    return mark_notification_read(db, notification_id=notification_id, user_id=user.id)


@router.post("/read-all", status_code=status.HTTP_204_NO_CONTENT)
def read_all_notifications(user: AuthenticatedUser, db: DBSession) -> Response:
    mark_all_notifications_read(db, user.id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
