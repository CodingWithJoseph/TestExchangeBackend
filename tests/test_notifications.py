from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from app.services.notifications import add_notification
from tests.conftest import OWNER_ID, TESTER_ID, auth_headers, create_profile


def test_notification_inbox_is_private_and_supports_read_state(
    client: TestClient,
    session_factory: sessionmaker[Session],
) -> None:
    create_profile(client, OWNER_ID, "notification-owner")
    create_profile(client, TESTER_ID, "notification-tester")
    entity_id = uuid4()
    with session_factory() as db:
        notification = add_notification(
            db,
            user_id=OWNER_ID,
            kind="submission_received",
            title="Evidence is ready",
            body="A tester submitted evidence for review.",
            entity_type="submission",
            entity_id=entity_id,
            idempotency_key=f"test:{entity_id}:notification",
        )
        notification_id = notification.id
        db.commit()

    owner_items = client.get("/api/v1/notifications", headers=auth_headers(OWNER_ID)).json()
    tester_items = client.get("/api/v1/notifications", headers=auth_headers(TESTER_ID)).json()
    assert owner_items[0]["title"] == "Evidence is ready"
    assert owner_items[0]["read_at"] is None
    assert tester_items == []

    hidden = client.post(
        f"/api/v1/notifications/{notification_id}/read",
        headers=auth_headers(TESTER_ID),
    )
    assert hidden.status_code == 404
    read = client.post(
        f"/api/v1/notifications/{notification_id}/read",
        headers=auth_headers(OWNER_ID),
    )
    assert read.status_code == 200
    assert read.json()["read_at"] is not None
    assert (
        client.post("/api/v1/notifications/read-all", headers=auth_headers(OWNER_ID)).status_code
        == 204
    )
