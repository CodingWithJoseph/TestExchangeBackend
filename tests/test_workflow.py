from uuid import UUID

from fastapi.testclient import TestClient

from app.models.enums import AssignmentStatus
from tests.conftest import (
    INTRUDER_ID,
    OWNER_ID,
    TESTER_ID,
    auth_headers,
    create_profile,
)


def campaign_payload() -> dict:
    return {
        "name": "NoteForge Recovery",
        "slug": "noteforge-offline-recovery",
        "platform": "android",
        "category": "Productivity",
        "public_summary": "Test offline note recovery after a device loses connectivity.",
        "public_tester_requirements": "Android users comfortable testing offline synchronization.",
        "minimum_version": "Android 10+",
        "target_testers": 2,
        "reward_credits": 4,
    }


def contract_payload() -> dict:
    return {
        "tester_instructions": (
            "Install the private build, create a note offline, and verify recovery "
            "after reconnecting."
        ),
        "access_instructions": "Open https://example.com/private-build after acceptance.",
        "device_requirements": "Android 10 or newer with airplane mode available.",
        "evidence_requirements": "Provide one note or screenshot for every required task.",
        "review_window_hours": 72,
        "tasks": [
            {
                "title": "Create an offline note",
                "instructions": (
                    "Enable airplane mode and create a note containing a unique phrase."
                ),
                "evidence_required": True,
            },
            {
                "title": "Verify recovery",
                "instructions": (
                    "Reconnect, reopen the application, and confirm the note remains available."
                ),
                "evidence_required": True,
            },
        ],
    }


def test_complete_private_testing_workflow(client: TestClient) -> None:
    create_profile(client, OWNER_ID, "campaign-owner")
    create_profile(client, TESTER_ID, "helpful-tester")
    create_profile(client, INTRUDER_ID, "outside-user")

    owner_headers = auth_headers(OWNER_ID)
    tester_headers = auth_headers(TESTER_ID)
    intruder_headers = auth_headers(INTRUDER_ID)

    response = client.post("/api/v1/campaigns", headers=owner_headers, json=campaign_payload())
    assert response.status_code == 201, response.text
    campaign = response.json()

    response = client.put(
        f"/api/v1/campaigns/{campaign['id']}/contract",
        headers=owner_headers,
        json=contract_payload(),
    )
    assert response.status_code == 200, response.text
    contract = response.json()
    task_ids = [task["id"] for task in contract["tasks"]]

    response = client.post(f"/api/v1/campaigns/{campaign['id']}/publish", headers=owner_headers)
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "published"

    public_response = client.get("/campaigns")
    assert public_response.status_code == 200
    assert public_response.json()[0]["slug"] == campaign["slug"]
    assert "tester_instructions" not in public_response.text

    response = client.post(
        f"/api/v1/campaigns/{campaign['id']}/assignments",
        headers=tester_headers,
        json={"application_note": "I can test this on Android 14 today."},
    )
    assert response.status_code == 201, response.text
    assignment = response.json()

    response = client.get(
        f"/api/v1/assignments/{assignment['id']}/contract", headers=tester_headers
    )
    assert response.status_code == 403

    response = client.get(
        f"/api/v1/assignments/{assignment['id']}/messages", headers=intruder_headers
    )
    assert response.status_code == 403

    response = client.post(f"/api/v1/assignments/{assignment['id']}/accept", headers=owner_headers)
    assert response.status_code == 200
    assert response.json()["status"] == AssignmentStatus.ACCEPTED

    response = client.post(f"/api/v1/assignments/{assignment['id']}/start", headers=tester_headers)
    assert response.status_code == 200
    response = client.get(
        f"/api/v1/assignments/{assignment['id']}/contract", headers=tester_headers
    )
    assert response.status_code == 200
    assert response.json()["access_instructions"].startswith("Open https://")

    submission_payload = {
        "summary": "The note survived the complete offline and reconnection sequence.",
        "items": [
            {"task_id": task_ids[0], "kind": "note", "note": "Created note TX-204 offline."},
            {
                "task_id": task_ids[1],
                "kind": "screenshot",
                "storage_key": f"{assignment['id']}/recovered-note.png",
            },
        ],
    }
    invalid_payload = {
        **submission_payload,
        "items": [
            submission_payload["items"][0],
            {
                **submission_payload["items"][1],
                "storage_key": "another-assignment/recovered-note.png",
            },
        ],
    }
    response = client.post(
        f"/api/v1/assignments/{assignment['id']}/submissions",
        headers=tester_headers,
        json=invalid_payload,
    )
    assert response.status_code == 422

    response = client.post(
        f"/api/v1/assignments/{assignment['id']}/submissions",
        headers=tester_headers,
        json=submission_payload,
    )
    assert response.status_code == 201, response.text
    first_submission = response.json()

    response = client.post(
        f"/api/v1/submissions/{first_submission['id']}/reviews",
        headers=tester_headers,
        json={"decision": "approved", "notes": "Trying to approve my own work."},
    )
    assert response.status_code == 403

    response = client.post(
        f"/api/v1/submissions/{first_submission['id']}/reviews",
        headers=owner_headers,
        json={
            "decision": "changes_requested",
            "notes": "Please include the restored note text after reconnecting.",
        },
    )
    assert response.status_code == 200, response.text

    response = client.get(
        f"/api/v1/assignments/{assignment['id']}/submissions", headers=owner_headers
    )
    assert response.status_code == 200, response.text
    assert [item["version"] for item in response.json()] == [1]
    assert response.json()[0]["items"][0]["note"] == "Created note TX-204 offline."
    response = client.get(
        f"/api/v1/submissions/{first_submission['id']}/reviews", headers=tester_headers
    )
    assert response.status_code == 200, response.text
    assert response.json()[0]["decision"] == "changes_requested"
    response = client.get(
        f"/api/v1/assignments/{assignment['id']}/submissions", headers=intruder_headers
    )
    assert response.status_code == 403

    submission_payload["summary"] = (
        "The note survived reconnection and the unique text TX-204 was still present."
    )
    submission_payload["items"][1] = {
        "task_id": task_ids[1],
        "kind": "note",
        "note": "Restored text confirmed: TX-204.",
    }
    response = client.post(
        f"/api/v1/assignments/{assignment['id']}/submissions",
        headers=tester_headers,
        json=submission_payload,
    )
    assert response.status_code == 201, response.text
    final_submission = response.json()
    assert final_submission["version"] == 2

    response = client.post(
        f"/api/v1/submissions/{final_submission['id']}/reviews",
        headers=owner_headers,
        json={"decision": "approved", "notes": "All required recovery evidence is present."},
    )
    assert response.status_code == 200, response.text

    response = client.get(
        f"/api/v1/assignments/{assignment['id']}/submissions", headers=tester_headers
    )
    assert [item["version"] for item in response.json()] == [2, 1]
    response = client.get(
        f"/api/v1/submissions/{final_submission['id']}/reviews", headers=tester_headers
    )
    assert response.json()[0]["decision"] == "approved"

    owner_balance = client.get("/api/v1/credits/balance", headers=owner_headers).json()
    tester_balance = client.get("/api/v1/credits/balance", headers=tester_headers).json()
    assert owner_balance["balance"] == 16
    assert tester_balance["balance"] == 28

    tester_ledger = client.get("/api/v1/credits/ledger", headers=tester_headers).json()
    assert [entry["entry_type"] for entry in tester_ledger] == ["reward", "signup_grant"]

    response = client.post(
        f"/api/v1/assignments/{assignment['id']}/messages",
        headers=tester_headers,
        json={"body": "Thanks—the recovery behavior is now documented."},
    )
    assert response.status_code == 201
    messages = client.get(
        f"/api/v1/assignments/{assignment['id']}/messages", headers=owner_headers
    ).json()
    assert messages[0]["sender_id"] == str(TESTER_ID)

    audit = client.get(
        f"/api/v1/assignments/{assignment['id']}/audit", headers=tester_headers
    ).json()
    actions = {event["action"] for event in audit}
    assert {
        "assignment.applied",
        "assignment.accepted",
        "assignment.started",
        "submission.changes_requested",
        "submission.approved",
    }.issubset(actions)

    response = client.post(
        f"/api/v1/assignments/{assignment['id']}/disputes",
        headers=tester_headers,
        json={
            "submission_id": final_submission["id"],
            "reason": "Opening a test dispute to verify the private audit workflow.",
        },
    )
    assert response.status_code == 409
    assert response.json()["detail"] == "Only a rejected assignment can be disputed"


def test_profile_username_and_campaign_ownership_are_enforced(client: TestClient) -> None:
    create_profile(client, OWNER_ID, "campaign-owner")
    response = client.put(
        "/api/v1/me/profile",
        headers=auth_headers(TESTER_ID),
        json={"username": "campaign-owner", "display_name": "Copy Cat"},
    )
    assert response.status_code == 409

    create_profile(client, TESTER_ID, "helpful-tester")
    campaign = client.post(
        "/api/v1/campaigns", headers=auth_headers(OWNER_ID), json=campaign_payload()
    ).json()
    response = client.patch(
        f"/api/v1/campaigns/{campaign['id']}",
        headers=auth_headers(TESTER_ID),
        json={"name": "Unauthorized rename"},
    )
    assert response.status_code == 403


def test_recruitment_controls_do_not_interrupt_active_testers(client: TestClient) -> None:
    third_tester_id = UUID("60000000-0000-0000-0000-000000000006")
    for user_id, username in (
        (OWNER_ID, "recruitment-owner"),
        (TESTER_ID, "active-tester"),
        (INTRUDER_ID, "declined-tester"),
        (third_tester_id, "pending-tester"),
    ):
        create_profile(client, user_id, username)

    campaign = client.post(
        "/api/v1/campaigns/launch",
        headers=auth_headers(OWNER_ID),
        json={"campaign": campaign_payload(), "contract": contract_payload()},
    ).json()

    declined = client.post(
        f"/api/v1/campaigns/{campaign['id']}/assignments",
        headers=auth_headers(INTRUDER_ID),
        json={"application_note": "Please review my application."},
    ).json()
    response = client.post(
        f"/api/v1/assignments/{declined['id']}/decline",
        headers=auth_headers(OWNER_ID),
    )
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "cancelled"

    active = client.post(
        f"/api/v1/campaigns/{campaign['id']}/assignments",
        headers=auth_headers(TESTER_ID),
        json={"application_note": "I can begin immediately."},
    ).json()
    pending = client.post(
        f"/api/v1/campaigns/{campaign['id']}/assignments",
        headers=auth_headers(third_tester_id),
        json={"application_note": "I can test next week."},
    ).json()
    assert (
        client.post(
            f"/api/v1/assignments/{active['id']}/accept",
            headers=auth_headers(OWNER_ID),
        ).status_code
        == 200
    )

    paused = client.post(
        f"/api/v1/campaigns/{campaign['id']}/transition",
        headers=auth_headers(OWNER_ID),
        json={"action": "pause"},
    )
    assert paused.json()["status"] == "paused"
    started = client.post(
        f"/api/v1/assignments/{active['id']}/start",
        headers=auth_headers(TESTER_ID),
    )
    assert started.status_code == 200, started.text
    assert started.json()["status"] == "in_progress"

    closed = client.post(
        f"/api/v1/campaigns/{campaign['id']}/transition",
        headers=auth_headers(OWNER_ID),
        json={"action": "close"},
    )
    assert closed.status_code == 200, closed.text
    assert closed.json()["status"] == "cancelled"
    assert (
        client.get(f"/api/v1/assignments/{active['id']}", headers=auth_headers(TESTER_ID)).json()[
            "status"
        ]
        == "in_progress"
    )
    assert (
        client.get(
            f"/api/v1/assignments/{pending['id']}", headers=auth_headers(third_tester_id)
        ).json()["status"]
        == "cancelled"
    )

    withdrawn = client.post(
        f"/api/v1/assignments/{active['id']}/withdraw",
        headers=auth_headers(TESTER_ID),
    )
    assert withdrawn.status_code == 200, withdrawn.text
    assert withdrawn.json()["status"] == "cancelled"
    owned_campaigns = client.get("/api/v1/campaigns/mine", headers=auth_headers(OWNER_ID)).json()
    assert owned_campaigns[0]["status"] == "completed"


def test_contract_duration_and_distinct_sessions_are_server_enforced(
    client: TestClient,
) -> None:
    create_profile(client, OWNER_ID, "duration-owner")
    create_profile(client, TESTER_ID, "duration-tester")
    campaign_input = campaign_payload()
    campaign_input["target_testers"] = 1
    contract_input = contract_payload()
    contract_input["minimum_duration_days"] = 1
    contract_input["required_sessions"] = 2
    campaign = client.post(
        "/api/v1/campaigns/launch",
        headers=auth_headers(OWNER_ID),
        json={"campaign": campaign_input, "contract": contract_input},
    ).json()
    contract = client.get(
        f"/api/v1/campaigns/{campaign['id']}/contract",
        headers=auth_headers(OWNER_ID),
    ).json()
    assignment = client.post(
        f"/api/v1/campaigns/{campaign['id']}/assignments",
        headers=auth_headers(TESTER_ID),
        json={"application_note": "I can complete a multi-day test."},
    ).json()
    assert (
        client.post(
            f"/api/v1/assignments/{assignment['id']}/accept",
            headers=auth_headers(OWNER_ID),
        ).status_code
        == 200
    )
    assert (
        client.post(
            f"/api/v1/assignments/{assignment['id']}/start",
            headers=auth_headers(TESTER_ID),
        ).status_code
        == 200
    )

    sessions = client.get(
        f"/api/v1/assignments/{assignment['id']}/sessions",
        headers=auth_headers(TESTER_ID),
    ).json()
    assert len(sessions) == 1
    duplicate_day = client.post(
        f"/api/v1/assignments/{assignment['id']}/sessions",
        headers=auth_headers(TESTER_ID),
        json={"note": "Trying to count the same UTC day twice."},
    )
    assert duplicate_day.status_code == 409
    assert duplicate_day.json()["detail"] == "A testing session is already recorded for today"

    submission = client.post(
        f"/api/v1/assignments/{assignment['id']}/submissions",
        headers=auth_headers(TESTER_ID),
        json={
            "summary": "I tried to submit before the required participation period ended.",
            "items": [
                {
                    "task_id": task["id"],
                    "kind": "note",
                    "note": f"Observed the required behavior for {task['title']}.",
                }
                for task in contract["tasks"]
            ],
        },
    )
    assert submission.status_code == 409
    assert submission.json()["detail"] == "This contract requires 1 full days of testing"
