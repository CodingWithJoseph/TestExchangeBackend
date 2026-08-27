from fastapi import APIRouter
from sqlalchemy import select

from app.api.deps import DBSession
from app.core.config import get_settings
from app.models import Campaign
from app.models.enums import CampaignStatus
from app.schemas.api import CampaignRead, HealthRead
from app.services.campaigns import list_public_campaigns
from app.services.common import DomainError

router = APIRouter(tags=["public"])


@router.get("/health", response_model=HealthRead)
def health() -> HealthRead:
    return HealthRead(status="ok", environment=get_settings().app_env)


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
    return campaign
