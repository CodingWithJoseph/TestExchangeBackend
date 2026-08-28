from fastapi.testclient import TestClient

from app.core.config import Settings, get_settings
from tests.conftest import (
    INTRUDER_ID,
    MODERATOR_ID,
    OWNER_ID,
    SECOND_MODERATOR_ID,
    TESTER_ID,
    auth_headers,
    create_profile,
)
from tests.test_workflow import campaign_payload, contract_payload


def create_disputed_assignment(client: TestClient) -> tuple[dict, dict]:
    owner_headers = auth_headers(OWNER_ID)
    tester_headers = auth_headers(TESTER_ID)
    campaign = client.post(
        "/api/v1/campaigns", headers=owner_headers, json=campaign_payload()
    ).json()
    contract = client.put(
        f"/api/v1/campaigns/{campaign['id']}/contract",
        headers=owner_headers,
        json=contract_payload(),
    ).json()
    assert (
        client.post(
            f"/api/v1/campaigns/{campaign['id']}/publish", headers=owner_headers
        ).status_code
        == 200
    )
    assignment = client.post(
        f"/api/v1/campaigns/{campaign['id']}/assignments",
        headers=tester_headers,
        json={"application_note": "I can test this recovery flow."},
    ).json()
    assert (
        client.post(
            f"/api/v1/assignments/{assignment['id']}/accept", headers=owner_headers
        ).status_code
        == 200
    )
    assert (
        client.post(
            f"/api/v1/assignments/{assignment['id']}/start", headers=tester_headers
        ).status_code
        == 200
    )
    submission = client.post(
        f"/api/v1/assignments/{assignment['id']}/submissions",
        headers=tester_headers,
        json={
            "summary": "I completed both recovery steps and recorded the observed result.",
            "items": [
                {
                    "task_id": contract["tasks"][0]["id"],
                    "kind": "note",
                    "note": "Created the offline note.",
                },
                {
                    "task_id": contract["tasks"][1]["id"],
                    "kind": "screenshot",
                    "storage_key": f"{assignment['id']}/recovery.png",
                },
            ],
        },
    ).json()
    assert (
        client.post(
            f"/api/v1/submissions/{submission['id']}/reviews",
            headers=owner_headers,
            json={
                "decision": "rejected",
                "notes": "The screenshot does not show the restored text clearly enough.",
            },
        ).status_code
        == 200
    )
    assert (
        client.post(
            f"/api/v1/assignments/{assignment['id']}/messages",
            headers=tester_headers,
            json={"body": "The image shows the note after reconnecting."},
        ).status_code
        == 201
    )
    dispute = client.post(
        f"/api/v1/assignments/{assignment['id']}/disputes",
        headers=tester_headers,
        json={
            "submission_id": submission["id"],
            "reason": "The submitted evidence satisfies the written recovery task.",
        },
    ).json()
    return assignment, dispute


def test_moderator_can_claim_review_and_resolve_private_dispute(client: TestClient) -> None:
    for user_id, username in (
        (OWNER_ID, "campaign-owner"),
        (TESTER_ID, "helpful-tester"),
        (INTRUDER_ID, "outside-user"),
        (MODERATOR_ID, "first-moderator"),
        (SECOND_MODERATOR_ID, "second-moderator"),
    ):
        create_profile(client, user_id, username)
    client.app.dependency_overrides[get_settings] = lambda: Settings(
        moderator_user_ids=[MODERATOR_ID, SECOND_MODERATOR_ID]
    )
    assignment, dispute = create_disputed_assignment(client)

    response = client.get("/api/v1/moderation/disputes", headers=auth_headers(INTRUDER_ID))
    assert response.status_code == 403

    response = client.get(
        "/api/v1/moderation/disputes?status=open", headers=auth_headers(MODERATOR_ID)
    )
    assert response.status_code == 200, response.text
    assert [item["id"] for item in response.json()] == [dispute["id"]]

    response = client.get(
        f"/api/v1/moderation/disputes/{dispute['id']}",
        headers=auth_headers(MODERATOR_ID),
    )
    assert response.status_code == 200, response.text
    case = response.json()
    assert case["assignment"]["id"] == assignment["id"]
    assert len(case["contract"]["tasks"]) == 2
    assert case["submissions"][0]["items"][1]["storage_key"].endswith("/recovery.png")
    assert case["reviews"][0]["decision"] == "rejected"
    assert case["messages"][0]["body"].startswith("The image")

    response = client.post(
        f"/api/v1/moderation/disputes/{dispute['id']}/claim",
        headers=auth_headers(MODERATOR_ID),
    )
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "under_review"
    assert response.json()["assigned_to"] == str(MODERATOR_ID)

    response = client.post(
        f"/api/v1/moderation/disputes/{dispute['id']}/claim",
        headers=auth_headers(SECOND_MODERATOR_ID),
    )
    assert response.status_code == 409
    response = client.post(
        f"/api/v1/moderation/disputes/{dispute['id']}/resolve",
        headers=auth_headers(SECOND_MODERATOR_ID),
        json={
            "outcome": "resolved",
            "resolution": "The evidence meets the written contract requirements.",
        },
    )
    assert response.status_code == 403

    response = client.post(
        f"/api/v1/moderation/disputes/{dispute['id']}/resolve",
        headers=auth_headers(MODERATOR_ID),
        json={
            "outcome": "resolved",
            "resolution": "The evidence meets the written contract requirements.",
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "resolved"
    assert response.json()["resolved_by"] == str(MODERATOR_ID)

    participant_disputes = client.get(
        "/api/v1/disputes/mine", headers=auth_headers(TESTER_ID)
    ).json()
    assert participant_disputes[0]["resolution"].startswith("The evidence")
    audit = client.get(
        f"/api/v1/assignments/{assignment['id']}/audit", headers=auth_headers(OWNER_ID)
    ).json()
    actions = {event["action"] for event in audit}
    assert {"dispute.claimed", "dispute.resolved"}.issubset(actions)
