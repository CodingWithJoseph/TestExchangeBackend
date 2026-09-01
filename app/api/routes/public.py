from typing import Annotated

from fastapi import APIRouter, Depends, status
from fastapi.responses import JSONResponse
from sqlalchemy import func, select, text
from sqlalchemy.exc import SQLAlchemyError

from app.api.deps import DBSession
from app.core.config import Settings, get_settings
from app.models import Assignment, Campaign
from app.models.enums import OCCUPIED_ASSIGNMENT_STATUSES, CampaignStatus
from app.schemas.api import BetaStatusRead, CampaignRead, HealthRead, WaitlistCreate, WaitlistRead
from app.services.campaigns import list_public_campaigns
from app.services.common import DomainError
from app.services.profiles import beta_status, join_waitlist

router = APIRouter(tags=["public"])
RuntimeSettings = Annotated[Settings, Depends(get_settings)]


@router.get("/health", response_model=HealthRead)
def health() -> HealthRead:
    return HealthRead(status="ok", environment=get_settings().app_env)


@router.get("/ready", response_model=HealthRead, responses={503: {"description": "Not ready"}})
def ready(db: DBSession) -> HealthRead | JSONResponse:
    settings = get_settings()
    try:
        db.execute(text("SELECT 1"))
        # Touch an application table so a database with unapplied migrations is not ready.
        db.scalar(select(Campaign.id).limit(1))
    except SQLAlchemyError:
        return JSONResponse(
            status_code=503,
            content={"status": "unavailable", "environment": settings.app_env},
        )
    return HealthRead(status="ready", environment=settings.app_env)


@router.get("/beta/status", response_model=BetaStatusRead)
def public_beta_status(db: DBSession, settings: RuntimeSettings) -> BetaStatusRead:
    claimed, remaining, is_full = beta_status(
        db,
        enabled=settings.public_beta_enabled,
        max_users=settings.public_beta_max_users,
    )
    return BetaStatusRead(
        enabled=settings.public_beta_enabled,
        max_users=settings.public_beta_max_users,
        claimed_seats=claimed,
        remaining_seats=remaining,
        is_full=is_full,
    )


@router.post("/beta/waitlist", response_model=WaitlistRead, status_code=status.HTTP_201_CREATED)
def waitlist(payload: WaitlistCreate, db: DBSession):
    return join_waitlist(db, payload.email)


@router.get("/campaigns", response_model=list[CampaignRead])
def public_campaigns(db: DBSession) -> list[Campaign]:
    return list_public_campaigns(db)


@router.get("/campaigns/{slug}", response_model=CampaignRead)
def public_campaign(slug: str, db: DBSession) -> Campaign:
    campaign = db.scalar(
        select(Campaign).where(Campaign.slug == slug, Campaign.status == CampaignStatus.PUBLISHED)
    )
    if campaign is None:
        raise DomainError("Campaign not found", 404)
    occupied_slots = db.scalar(
        select(func.count(Assignment.id)).where(
            Assignment.campaign_id == campaign.id,
            Assignment.status.in_(OCCUPIED_ASSIGNMENT_STATUSES),
        )
    )
    if occupied_slots is not None and occupied_slots >= campaign.target_testers:
        raise DomainError("Campaign not found", 404)
    return campaign
