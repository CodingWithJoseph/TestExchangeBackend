from typing import Annotated

from fastapi import APIRouter, Depends

from app.api.deps import DBSession
from app.core.auth import AuthenticatedUser
from app.core.config import Settings, get_settings
from app.models import Profile
from app.schemas.api import CapabilitiesRead, ProfileRead, ProfileUpsert
from app.services.common import DomainError
from app.services.profiles import upsert_profile

router = APIRouter(prefix="/me", tags=["profile"])
RuntimeSettings = Annotated[Settings, Depends(get_settings)]


@router.get("/profile", response_model=ProfileRead)
def read_profile(user: AuthenticatedUser, db: DBSession) -> Profile:
    profile = db.get(Profile, user.id)
    if profile is None:
        raise DomainError("Profile not found", 404)
    return profile


@router.put("/profile", response_model=ProfileRead)
def save_profile(
    payload: ProfileUpsert,
    user: AuthenticatedUser,
    db: DBSession,
    settings: RuntimeSettings,
) -> Profile:
    return upsert_profile(
        db,
        user_id=user.id,
        email=user.email,
        payload=payload,
        signup_credit_grant=settings.signup_credit_grant,
        public_beta_enabled=settings.public_beta_enabled,
        public_beta_max_users=settings.public_beta_max_users,
    )


@router.get("/capabilities", response_model=CapabilitiesRead)
def capabilities(user: AuthenticatedUser, settings: RuntimeSettings) -> CapabilitiesRead:
    return CapabilitiesRead(is_moderator=user.id in settings.moderator_user_ids)
