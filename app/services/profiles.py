from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Profile
from app.models.enums import CreditEntryType
from app.schemas.api import ProfileUpsert
from app.services.common import DomainError, add_audit_event
from app.services.credits import ensure_credit_account, record_credit_entry


def upsert_profile(
    db: Session,
    *,
    user_id: UUID,
    email: str | None,
    payload: ProfileUpsert,
    signup_credit_grant: int,
) -> Profile:
    username_owner = db.scalar(select(Profile).where(Profile.username == payload.username))
    if username_owner is not None and username_owner.id != user_id:
        raise DomainError("That username is already taken", 409)

    profile = db.get(Profile, user_id)
    created = profile is None
    values = payload.model_dump(mode="json")
    if created:
        profile = Profile(id=user_id, email=email, **values)
        db.add(profile)
        db.flush()
        ensure_credit_account(db, user_id)
        if signup_credit_grant > 0:
            record_credit_entry(
                db,
                user_id=user_id,
                delta=signup_credit_grant,
                entry_type=CreditEntryType.SIGNUP_GRANT,
                idempotency_key=f"profile:{user_id}:signup-grant",
                reference_type="profile",
                reference_id=user_id,
                note="Private beta starting credits",
                created_by=user_id,
            )
    else:
        profile.email = email
        for field, value in values.items():
            setattr(profile, field, value)

    add_audit_event(
        db,
        actor_id=user_id,
        action="profile.created" if created else "profile.updated",
        entity_type="profile",
        entity_id=user_id,
    )
    db.flush()
    return profile
