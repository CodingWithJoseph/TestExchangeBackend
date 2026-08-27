import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from app.models import CreditAccount, CreditLedgerEntry, Profile
from app.models.entities import ImmutableLedgerError
from app.models.enums import CreditEntryType
from app.services.credits import record_credit_entry
from tests.conftest import OWNER_ID, auth_headers, create_profile
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


def test_campaign_cannot_reserve_more_credits_than_owner_has(client: TestClient) -> None:
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
