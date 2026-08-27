from fastapi import APIRouter

from app.api.deps import DBSession
from app.core.auth import AuthenticatedUser
from app.core.config import get_settings
from app.models import Profile
from app.schemas.api import ProfileRead, ProfileUpsert
from app.services.common import get_profile
from app.services.profiles import upsert_profile

router = APIRouter(prefix="/me", tags=["profile"])


@router.get("/profile", response_model=ProfileRead)
def read_profile(user: AuthenticatedUser, db: DBSession) -> Profile:
    return get_profile(db, user.id)


@router.put("/profile", response_model=ProfileRead)
def save_profile(payload: ProfileUpsert, user: AuthenticatedUser, db: DBSession) -> Profile:
    return upsert_profile(
        db,
        user_id=user.id,
        email=user.email,
        payload=payload,
        signup_credit_grant=get_settings().signup_credit_grant,
    )
