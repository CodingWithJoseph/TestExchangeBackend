from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import BetaProgramState, Profile, WaitlistEntry
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
    public_beta_enabled: bool,
    public_beta_max_users: int,
) -> Profile:
    username_owner = db.scalar(select(Profile).where(Profile.username == payload.username))
    if username_owner is not None and username_owner.id != user_id:
        raise DomainError("That username is already taken", 409)

    profile = db.get(Profile, user_id)
    created = profile is None
    values = payload.model_dump(mode="json")
    if created:
        if not public_beta_enabled:
            raise DomainError("Public beta registration is temporarily paused", 403)
        beta_state = db.scalar(
            select(BetaProgramState).where(BetaProgramState.id == 1).with_for_update()
        )
        if beta_state is None:
            # Production migrations seed this singleton. This fallback keeps fresh local
            # metadata-only databases usable without weakening the PostgreSQL release path.
            beta_state = BetaProgramState(
                id=1,
                claimed_seats=db.scalar(select(func.count(Profile.id))) or 0,
            )
            db.add(beta_state)
            db.flush()
        if beta_state.claimed_seats >= public_beta_max_users:
            raise DomainError(
                "The public beta is full; join the waitlist for the next opening",
                409,
            )
        beta_state.claimed_seats += 1
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
                note="Public beta starting credits",
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


def beta_status(db: Session, *, enabled: bool, max_users: int) -> tuple[int, int, bool]:
    state = db.get(BetaProgramState, 1)
    claimed = (
        state.claimed_seats if state is not None else db.scalar(select(func.count(Profile.id))) or 0
    )
    remaining = max(max_users - claimed, 0) if enabled else 0
    return claimed, remaining, not enabled or claimed >= max_users


def join_waitlist(db: Session, email: str) -> WaitlistEntry:
    existing = db.scalar(select(WaitlistEntry).where(WaitlistEntry.email == email))
    if existing is not None:
        return existing
    try:
        with db.begin_nested():
            entry = WaitlistEntry(email=email)
            db.add(entry)
            db.flush()
        return entry
    except IntegrityError:
        existing = db.scalar(select(WaitlistEntry).where(WaitlistEntry.email == email))
        if existing is None:
            raise
        return existing


def list_waitlist(db: Session) -> list[WaitlistEntry]:
    return list(db.scalars(select(WaitlistEntry).order_by(WaitlistEntry.created_at)))


def list_participants(db: Session) -> list[Profile]:
    return list(db.scalars(select(Profile).order_by(Profile.created_at.desc()).limit(500)))


def suspend_participant(
    db: Session, *, participant_id: UUID, moderator_id: UUID, reason: str
) -> Profile:
    if participant_id == moderator_id:
        raise DomainError("Moderators cannot suspend their own account", 409)
    profile = db.scalar(select(Profile).where(Profile.id == participant_id).with_for_update())
    if profile is None:
        raise DomainError("Participant not found", 404)
    profile.is_suspended = True
    profile.suspended_at = datetime.now(UTC)
    profile.suspension_reason = reason
    add_audit_event(
        db,
        actor_id=moderator_id,
        action="profile.suspended",
        entity_type="profile",
        entity_id=profile.id,
        details={"reason": reason},
    )
    db.flush()
    return profile


def restore_participant(db: Session, *, participant_id: UUID, moderator_id: UUID) -> Profile:
    profile = db.scalar(select(Profile).where(Profile.id == participant_id).with_for_update())
    if profile is None:
        raise DomainError("Participant not found", 404)
    profile.is_suspended = False
    profile.suspended_at = None
    profile.suspension_reason = None
    add_audit_event(
        db,
        actor_id=moderator_id,
        action="profile.restored",
        entity_type="profile",
        entity_id=profile.id,
    )
    db.flush()
    return profile
