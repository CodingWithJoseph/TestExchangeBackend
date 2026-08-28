from uuid import UUID

from fastapi.testclient import TestClient

from tests.conftest import INTRUDER_ID, OWNER_ID, TESTER_ID, auth_headers, create_profile

SECOND_TESTER_ID = UUID("60000000-0000-0000-0000-000000000006")


def _campaign_payload() -> dict:
    return {
        "name": "Quality Check Demo",
        "slug": "quality-check-demo",
        "platform": "web",
        "category": "Productivity",
        "public_summary": "Test a focused workflow and report what happened at each step.",
        "public_tester_requirements": "Any tester who can follow written steps and share evidence.",
        "target_testers": 2,
        "reward_credits": 3,
    }


def _contract_payload() -> dict:
    return {
        "tester_instructions": (
            "Follow the task, record the result, and explain anything unexpected."
        ),
        "access_instructions": "Open the private build after your assignment is accepted.",
        "device_requirements": "Use a current browser with developer tools available.",
        "evidence_requirements": "Link each required task to a note, screenshot, or recording.",
        "review_window_hours": 72,
        "tasks": [
            {
                "title": "Complete the workflow",
                "instructions": (
                    "Create a sample record, save it, and reopen it to verify persistence."
                ),
                "evidence_required": True,
            }
        ],
    }


def _create_assignment(client: TestClient, tester_id: UUID) -> tuple[dict, str]:
    campaign_response = client.post(
        "/api/v1/campaigns", headers=auth_headers(OWNER_ID), json=_campaign_payload()
    )
    assert campaign_response.status_code == 201, campaign_response.text
    campaign = campaign_response.json()

    contract_response = client.put(
        f"/api/v1/campaigns/{campaign['id']}/contract",
        headers=auth_headers(OWNER_ID),
        json=_contract_payload(),
    )
    assert contract_response.status_code == 200, contract_response.text
    task_id = contract_response.json()["tasks"][0]["id"]

    publish_response = client.post(
        f"/api/v1/campaigns/{campaign['id']}/publish", headers=auth_headers(OWNER_ID)
    )
    assert publish_response.status_code == 200, publish_response.text

    apply_response = client.post(
        f"/api/v1/campaigns/{campaign['id']}/assignments",
        headers=auth_headers(tester_id),
        json={"application_note": "I can complete this test and document the result."},
    )
    assert apply_response.status_code == 201, apply_response.text
    assignment = apply_response.json()

    accept_response = client.post(
        f"/api/v1/assignments/{assignment['id']}/accept", headers=auth_headers(OWNER_ID)
    )
    assert accept_response.status_code == 200, accept_response.text
    start_response = client.post(
        f"/api/v1/assignments/{assignment['id']}/start", headers=auth_headers(tester_id)
    )
    assert start_response.status_code == 200, start_response.text
    return assignment, task_id


def test_quality_check_explains_weak_submission_and_preserves_privacy(
    client: TestClient,
) -> None:
    create_profile(client, OWNER_ID, "quality-owner")
    create_profile(client, TESTER_ID, "quality-tester")
    create_profile(client, INTRUDER_ID, "quality-outsider")
    assignment, task_id = _create_assignment(client, TESTER_ID)

    response = client.post(
        f"/api/v1/assignments/{assignment['id']}/submissions",
        headers=auth_headers(TESTER_ID),
        json={
            "summary": "Works fine and no issues.",
            "items": [
                {"task_id": task_id, "kind": "note", "note": "Looks okay."},
            ],
        },
    )
    assert response.status_code == 201, response.text
    submission_id = response.json()["id"]

    response = client.get(
        f"/api/v1/submissions/{submission_id}/quality-check",
        headers=auth_headers(TESTER_ID),
    )
    assert response.status_code == 200, response.text
    quality = response.json()
    assert quality["status"] == "needs_attention"
    assert quality["score"] == 50
    assert {item["code"] for item in quality["checks"] if item["status"] == "flagged"} == {
        "summary_specificity",
        "concrete_observation",
    }
    assert "Advisory only" in quality["disclaimer"]

    response = client.get(
        f"/api/v1/submissions/{submission_id}/quality-check",
        headers=auth_headers(OWNER_ID),
    )
    assert response.status_code == 200
    response = client.get(
        f"/api/v1/submissions/{submission_id}/quality-check",
        headers=auth_headers(INTRUDER_ID),
    )
    assert response.status_code == 403


def test_quality_check_marks_specific_submission_ready_then_reviewed(
    client: TestClient,
) -> None:
    create_profile(client, OWNER_ID, "ready-owner")
    create_profile(client, SECOND_TESTER_ID, "ready-tester")
    assignment, task_id = _create_assignment(client, SECOND_TESTER_ID)

    response = client.post(
        f"/api/v1/assignments/{assignment['id']}/submissions",
        headers=auth_headers(SECOND_TESTER_ID),
        json={
            "summary": (
                "Creating a sample record succeeded, and reopening the workflow showed the "
                "same saved value after a fresh page load."
            ),
            "items": [
                {
                    "task_id": task_id,
                    "kind": "note",
                    "note": (
                        "On the records page, the sample entry remained visible after saving "
                        "and reopening the page."
                    ),
                }
            ],
        },
    )
    assert response.status_code == 201, response.text
    submission_id = response.json()["id"]

    response = client.get(
        f"/api/v1/submissions/{submission_id}/quality-check",
        headers=auth_headers(OWNER_ID),
    )
    assert response.status_code == 200, response.text
    quality = response.json()
    assert quality["status"] == "ready_for_review"
    assert quality["score"] == 100
    assert all(item["status"] == "passed" for item in quality["checks"])

    response = client.post(
        f"/api/v1/submissions/{submission_id}/reviews",
        headers=auth_headers(OWNER_ID),
        json={"decision": "approved", "notes": "The evidence is specific and reproducible."},
    )
    assert response.status_code == 200, response.text

    response = client.get(
        f"/api/v1/submissions/{submission_id}/quality-check",
        headers=auth_headers(SECOND_TESTER_ID),
    )
    assert response.status_code == 200
    assert response.json()["status"] == "already_reviewed"
