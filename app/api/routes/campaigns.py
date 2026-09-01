from uuid import UUID

from fastapi import APIRouter, status

from app.api.deps import DBSession
from app.core.auth import AuthenticatedUser
from app.models import Campaign
from app.schemas.api import (
    CampaignCreate,
    CampaignLaunch,
    CampaignRead,
    CampaignTransition,
    CampaignUpdate,
    ContractRead,
    ContractTaskRead,
    ContractUpsert,
)
from app.services.campaigns import (
    create_campaign,
    get_contract,
    launch_campaign,
    list_owned_campaigns,
    publish_campaign,
    transition_campaign,
    update_campaign,
    upsert_contract,
)
from app.services.common import get_campaign, require_campaign_owner

router = APIRouter(prefix="/campaigns", tags=["campaigns"])


def contract_response(db: DBSession, campaign_id: UUID) -> ContractRead:
    contract, tasks = get_contract(db, campaign_id)
    return ContractRead(
        id=contract.id,
        campaign_id=contract.campaign_id,
        version=contract.version,
        tester_instructions=contract.tester_instructions,
        access_instructions=contract.access_instructions,
        device_requirements=contract.device_requirements,
        evidence_requirements=contract.evidence_requirements,
        review_window_hours=contract.review_window_hours,
        minimum_duration_days=contract.minimum_duration_days,
        required_sessions=contract.required_sessions,
        status=contract.status,
        locked_at=contract.locked_at,
        tasks=[ContractTaskRead.model_validate(task) for task in tasks],
    )


@router.post("", response_model=CampaignRead, status_code=status.HTTP_201_CREATED)
def create(payload: CampaignCreate, user: AuthenticatedUser, db: DBSession) -> Campaign:
    return create_campaign(db, owner_id=user.id, payload=payload)


@router.post("/launch", response_model=CampaignRead, status_code=status.HTTP_201_CREATED)
def launch(payload: CampaignLaunch, user: AuthenticatedUser, db: DBSession) -> Campaign:
    return launch_campaign(db, owner_id=user.id, payload=payload)


@router.get("/mine", response_model=list[CampaignRead])
def mine(user: AuthenticatedUser, db: DBSession) -> list[Campaign]:
    return list_owned_campaigns(db, user.id)


@router.patch("/{campaign_id}", response_model=CampaignRead)
def update(
    campaign_id: UUID,
    payload: CampaignUpdate,
    user: AuthenticatedUser,
    db: DBSession,
) -> Campaign:
    return update_campaign(db, campaign_id=campaign_id, owner_id=user.id, payload=payload)


@router.put("/{campaign_id}/contract", response_model=ContractRead)
def save_contract(
    campaign_id: UUID,
    payload: ContractUpsert,
    user: AuthenticatedUser,
    db: DBSession,
) -> ContractRead:
    upsert_contract(db, campaign_id=campaign_id, owner_id=user.id, payload=payload)
    return contract_response(db, campaign_id)


@router.get("/{campaign_id}/contract", response_model=ContractRead)
def read_contract(campaign_id: UUID, user: AuthenticatedUser, db: DBSession) -> ContractRead:
    campaign = get_campaign(db, campaign_id)
    require_campaign_owner(campaign, user.id)
    return contract_response(db, campaign_id)


@router.post("/{campaign_id}/publish", response_model=CampaignRead)
def publish(campaign_id: UUID, user: AuthenticatedUser, db: DBSession) -> Campaign:
    return publish_campaign(db, campaign_id=campaign_id, owner_id=user.id)


@router.post("/{campaign_id}/transition", response_model=CampaignRead)
def transition(
    campaign_id: UUID,
    payload: CampaignTransition,
    user: AuthenticatedUser,
    db: DBSession,
) -> Campaign:
    return transition_campaign(
        db,
        campaign_id=campaign_id,
        owner_id=user.id,
        payload=payload,
    )
