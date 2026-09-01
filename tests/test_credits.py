import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from app.models import CreditAccount, CreditLedgerEntry, Profile
from app.models.entities import ImmutableLedgerError
from app.models.enums import CreditEntryType
from app.services.credits import record_credit_entry
from tests.conftest import INTRUDER_ID, OWNER_ID, TESTER_ID, auth_headers, create_profile
from tests.test_workflow import campaign_payload, contract_payload


def test_credit_ledger_entries_cannot_be_changed(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as db:
        db.add(
            Profile(
                id=OWNER_ID,
                email="owner@example.com",
                username="owner",
                display_name="Owner",
            )
        )
        db.flush()
        db.add(CreditAccount(user_id=OWNER_ID, balance=0))
        db.flush()
        entry = record_credit_entry(
            db,
            user_id=OWNER_ID,
            delta=24,
            entry_type=CreditEntryType.SIGNUP_GRANT,
            idempotency_key="owner:initial",
        )
        db.commit()

        entry.delta = 25
        with pytest.raises(ImmutableLedgerError):
            db.commit()
        db.rollback()

        stored = db.get(CreditLedgerEntry, entry.id)
        assert stored is not None
        assert stored.delta == 24


def test_campaign_cannot_spend_more_credits_than_owner_has(client: TestClient) -> None:
    create_profile(client, OWNER_ID, "campaign-owner")
    payload = campaign_payload()
    payload["target_testers"] = 7
    owner_headers = auth_headers(OWNER_ID)
    campaign = client.post("/api/v1/campaigns", headers=owner_headers, json=payload).json()
    response = client.put(
        f"/api/v1/campaigns/{campaign['id']}/contract",
        headers=owner_headers,
        json=contract_payload(),
    )
    assert response.status_code == 200

    response = client.post(f"/api/v1/campaigns/{campaign['id']}/publish", headers=owner_headers)

    assert response.status_code == 409
    assert response.json()["detail"] == "Insufficient credits"
    balance = client.get("/api/v1/credits/balance", headers=owner_headers).json()
    assert balance["balance"] == 24


def test_publishing_is_atomic_and_campaign_spend_is_never_refunded(
    client: TestClient,
) -> None:
    create_profile(client, OWNER_ID, "campaign-owner")
    owner_headers = auth_headers(OWNER_ID)
    payload = campaign_payload()
    payload["target_testers"] = 1

    response = client.post(
        "/api/v1/campaigns/launch",
        headers=owner_headers,
        json={"campaign": payload, "contract": contract_payload()},
    )

    assert response.status_code == 201, response.text
    campaign = response.json()
    assert campaign["status"] == "published"
    assert client.get("/api/v1/credits/balance", headers=owner_headers).json()["balance"] == 20
    ledger = client.get("/api/v1/credits/ledger", headers=owner_headers).json()
    assert [entry["entry_type"] for entry in ledger] == ["posting", "signup_grant"]
    assert ledger[0]["delta"] == -4
    assert "permanent spend" in ledger[0]["note"]

    for action, expected_status in (
        ("pause", "paused"),
        ("resume", "published"),
        ("close", "cancelled"),
    ):
        transition = client.post(
            f"/api/v1/campaigns/{campaign['id']}/transition",
            headers=owner_headers,
            json={"action": action},
        )
        assert transition.status_code == 200, transition.text
        assert transition.json()["status"] == expected_status

    assert client.get("/api/v1/credits/balance", headers=owner_headers).json()["balance"] == 20
    ledger = client.get("/api/v1/credits/ledger", headers=owner_headers).json()
    assert all(entry["entry_type"] != "refund" for entry in ledger)
    assert client.get("/campaigns").json() == []


def test_atomic_launch_rolls_back_draft_and_contract_when_funding_fails(
    client: TestClient,
) -> None:
    create_profile(client, OWNER_ID, "campaign-owner")
    payload = campaign_payload()
    payload["target_testers"] = 7

    response = client.post(
        "/api/v1/campaigns/launch",
        headers=auth_headers(OWNER_ID),
        json={"campaign": payload, "contract": contract_payload()},
    )

    assert response.status_code == 409
    assert response.json()["detail"] == "Insufficient credits"
    assert client.get("/api/v1/campaigns/mine", headers=auth_headers(OWNER_ID)).json() == []


def test_filled_campaign_rejects_more_accepts_and_disappears_from_public_list(
    client: TestClient,
) -> None:
    for user_id, username in (
        (OWNER_ID, "campaign-owner"),
        (TESTER_ID, "first-tester"),
        (INTRUDER_ID, "second-tester"),
    ):
        create_profile(client, user_id, username)
    payload = campaign_payload()
    payload["target_testers"] = 1
    campaign = client.post(
        "/api/v1/campaigns/launch",
        headers=auth_headers(OWNER_ID),
        json={"campaign": payload, "contract": contract_payload()},
    ).json()
    assignments = []
    for tester_id in (TESTER_ID, INTRUDER_ID):
        response = client.post(
            f"/api/v1/campaigns/{campaign['id']}/assignments",
            headers=auth_headers(tester_id),
            json={"application_note": "I have the required environment."},
        )
        assert response.status_code == 201, response.text
        assignments.append(response.json())

    first = client.post(
        f"/api/v1/assignments/{assignments[0]['id']}/accept",
        headers=auth_headers(OWNER_ID),
    )
    second = client.post(
        f"/api/v1/assignments/{assignments[1]['id']}/accept",
        headers=auth_headers(OWNER_ID),
    )

    assert first.status_code == 200
    assert second.status_code == 409
    assert second.json()["detail"] == "This campaign already has its target number of testers"
    assert client.get("/campaigns").json() == []
