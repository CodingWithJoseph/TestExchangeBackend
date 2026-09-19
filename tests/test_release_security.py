from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import httpx
import pytest
from sqlalchemy import select

from app.core.config import Settings, get_settings
from app.models import AuditEvent, EvidenceSubmission, Profile
from app.services.common import DomainError
from app.services.evidence import sign_storage_evidence
from tests.conftest import (
    INTRUDER_ID,
    MODERATOR_ID,
    OWNER_ID,
    TESTER_ID,
    auth_headers,
    create_profile,
)
from tests.test_moderation import create_disputed_assignment
from tests.test_workflow import campaign_payload, contract_payload


def pending_assignment(client):
    for uid, name in ((OWNER_ID, "owner"), (TESTER_ID, "tester")):
        create_profile(client, uid, name)
    result = client.post(
        "/api/v1/campaigns/launch",
        headers=auth_headers(OWNER_ID),
        json={
            "campaign": campaign_payload(),
            "contract": contract_payload(),
        },
    )
    assert result.status_code == 201, result.text
    campaign = result.json()
    contract = client.get(
        f"/api/v1/campaigns/{campaign['id']}/contract", headers=auth_headers(OWNER_ID)
    ).json()
    response = client.post(
        f"/api/v1/campaigns/{campaign['id']}/assignments", headers=auth_headers(TESTER_ID), json={}
    )
    assert response.status_code == 201, response.text
    return campaign, contract, response.json()


@pytest.mark.parametrize("action", ["withdraw", "decline", "close"])
def test_unaccepted_cancelled_tester_cannot_read_private_contract(client, action):
    campaign, _, assignment = pending_assignment(client)
    path = f"/api/v1/assignments/{assignment['id']}"
    assert client.get(path + "/contract", headers=auth_headers(TESTER_ID)).status_code == 403
    if action == "close":
        response = client.post(
            f"/api/v1/campaigns/{campaign['id']}/transition",
            headers=auth_headers(OWNER_ID),
            json={"action": "close"},
        )
    else:
        response = client.post(
            path + "/" + action,
            headers=auth_headers(TESTER_ID if action == "withdraw" else OWNER_ID),
        )
    assert response.status_code == 200, response.text
    state = client.get(path, headers=auth_headers(TESTER_ID)).json()
    assert state["status"] == "cancelled" and state["accepted_at"] is None
    assert client.get(path + "/contract", headers=auth_headers(TESTER_ID)).status_code == 403
    assert client.get(path + "/contract", headers=auth_headers(OWNER_ID)).status_code == 200


def test_accepted_tester_keeps_historical_contract_after_withdrawal(client):
    _, _, assignment = pending_assignment(client)
    path = f"/api/v1/assignments/{assignment['id']}"
    assert client.post(path + "/accept", headers=auth_headers(OWNER_ID)).status_code == 200
    assert client.post(path + "/withdraw", headers=auth_headers(TESTER_ID)).status_code == 200
    assert client.get(path + "/contract", headers=auth_headers(TESTER_ID)).status_code == 200


def submitted_assignment(client):
    _, contract, assignment = pending_assignment(client)
    path = f"/api/v1/assignments/{assignment['id']}"
    assert client.post(path + "/accept", headers=auth_headers(OWNER_ID)).status_code == 200
    assert client.post(path + "/start", headers=auth_headers(TESTER_ID)).status_code == 200
    response = client.post(
        path + "/submissions",
        headers=auth_headers(TESTER_ID),
        json={
            "summary": "Completed the tasks and recorded the actual results.",
            "items": [
                {"task_id": task["id"], "kind": "note", "note": "Observed the required behavior."}
                for task in contract["tasks"]
            ],
        },
    )
    assert response.status_code == 201, response.text
    return path, response.json()


@pytest.mark.parametrize("award", [True, False])
def test_overdue_review_requires_deadline_then_moderator_settles_once(
    client, session_factory, award
):
    path, submission = submitted_assignment(client)
    create_profile(client, MODERATOR_ID, "moderator")
    client.app.dependency_overrides[get_settings] = lambda: Settings(
        moderator_user_ids=[MODERATOR_ID]
    )
    payload = {
        "submission_id": submission["id"],
        "reason": "The owner has not reviewed my completed work.",
    }
    assert (
        client.post(path + "/disputes", headers=auth_headers(TESTER_ID), json=payload).status_code
        == 409
    )
    with session_factory.begin() as db:
        record = db.get(EvidenceSubmission, UUID(submission["id"]))
        record.submitted_at = datetime.now(UTC) - timedelta(hours=73)
    response = client.post(path + "/disputes", headers=auth_headers(TESTER_ID), json=payload)
    assert response.status_code == 201, response.text
    dispute_path = f"/api/v1/moderation/disputes/{response.json()['id']}"
    assert (
        client.post(path + "/disputes", headers=auth_headers(TESTER_ID), json=payload).status_code
        == 409
    )
    review = client.post(
        f"/api/v1/submissions/{submission['id']}/reviews",
        headers=auth_headers(OWNER_ID),
        json={"decision": "approved", "notes": "Late owner approval attempt."},
    )
    assert review.status_code == 409
    assert (
        client.post(dispute_path + "/claim", headers=auth_headers(MODERATOR_ID)).status_code == 200
    )
    resolution = {
        "outcome": "resolved" if award else "rejected",
        "remedy": "award_tester" if award else "none",
        "resolution": "The moderator compared the submitted work to the agreed contract.",
    }
    response = client.post(
        dispute_path + "/resolve", headers=auth_headers(MODERATOR_ID), json=resolution
    )
    assert response.status_code == 200, response.text
    assert (
        client.post(
            dispute_path + "/resolve", headers=auth_headers(MODERATOR_ID), json=resolution
        ).status_code
        == 409
    )
    assert client.get(path, headers=auth_headers(TESTER_ID)).json()["status"] == (
        "approved" if award else "rejected"
    )
    assert client.get("/api/v1/credits/balance", headers=auth_headers(TESTER_ID)).json()[
        "balance"
    ] == (28 if award else 24)


def test_moderator_evidence_is_case_scoped_audited_and_denies_other_users(
    client, session_factory, monkeypatch
):
    for uid, name in (
        (OWNER_ID, "owner"),
        (TESTER_ID, "tester"),
        (MODERATOR_ID, "moderator"),
        (INTRUDER_ID, "outside"),
    ):
        create_profile(client, uid, name)
    client.app.dependency_overrides[get_settings] = lambda: Settings(
        moderator_user_ids=[MODERATOR_ID]
    )
    _, dispute = create_disputed_assignment(client)
    case = client.get(
        f"/api/v1/moderation/disputes/{dispute['id']}", headers=auth_headers(MODERATOR_ID)
    ).json()
    item = next(item for item in case["submissions"][0]["items"] if item["storage_key"])
    signed = []

    def signer(settings, key):
        signed.append(key)
        return "https://example.supabase.co/storage/v1/object/sign/test-evidence/test?token=dummy"

    monkeypatch.setattr("app.services.evidence.sign_storage_evidence", signer)
    path = f"/api/v1/moderation/disputes/{dispute['id']}/evidence/{item['id']}/url"
    for uid in (OWNER_ID, TESTER_ID, INTRUDER_ID):
        assert client.post(path, headers=auth_headers(uid)).status_code == 403
    assert (
        client.post(
            path.replace(dispute["id"], str(uuid4())), headers=auth_headers(MODERATOR_ID)
        ).status_code
        == 404
    )
    assert signed == []
    response = client.post(path, headers=auth_headers(MODERATOR_ID))
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    assert response.json()["expires_in"] == 60
    assert signed == [item["storage_key"]]
    with session_factory.begin() as db:
        audit = db.scalar(
            select(AuditEvent).where(AuditEvent.action == "moderation.evidence_opened")
        )
        assert audit.actor_id == MODERATOR_ID and audit.details["evidence_id"] == item["id"]
        assert "token" not in str(audit.details)
        db.get(Profile, MODERATOR_ID).is_suspended = True
    assert client.post(path, headers=auth_headers(MODERATOR_ID)).status_code == 403


def test_storage_signing_uses_server_credentials_and_rejects_unexpected_urls(monkeypatch):
    settings = Settings(
        supabase_url="https://example.supabase.co", supabase_service_role_key="dummy-service-secret"
    )

    def post(url, **kwargs):
        assert kwargs["headers"]["Authorization"] == "Bearer dummy-service-secret"
        assert kwargs["json"] == {"expiresIn": 60}
        assert kwargs["follow_redirects"] is False
        return httpx.Response(
            200,
            json={"signedURL": "https://untrusted.example/token"},
            request=httpx.Request("POST", url),
        )

    monkeypatch.setattr("app.services.evidence.httpx.post", post)
    with pytest.raises(DomainError) as error:
        sign_storage_evidence(settings, "assignment/file.png")
    assert error.value.status_code == 502
    assert "dummy-service-secret" not in str(error.value)
    assert "dummy-service-secret" not in repr(settings)


def test_storage_signing_fails_closed_when_not_configured():
    with pytest.raises(DomainError) as error:
        sign_storage_evidence(
            Settings(supabase_url=None, supabase_service_role_key=None), "assignment/file.png"
        )
    assert error.value.status_code == 503
