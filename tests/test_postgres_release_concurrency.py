import os
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Barrier
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.orm import sessionmaker

from app.db.session import normalize_database_url
from app.models import Assignment, ContractTask, CreditLedgerEntry
from app.models import TestingContract as Contract
from app.models.enums import AssignmentStatus
from app.schemas.api import (
    AssignmentApply,
    CampaignLaunch,
    DisputeCreate,
    ProfileUpsert,
    ReviewCreate,
    SubmissionCreate,
)
from app.services.campaigns import launch_campaign
from app.services.common import DomainError
from app.services.profiles import upsert_profile
from app.services.workflow import (
    accept_assignment,
    apply_to_campaign,
    create_review,
    create_submission,
    open_dispute,
    start_assignment,
)
from tests.test_workflow import campaign_payload, contract_payload

database_url = os.getenv("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not database_url, reason="Requires disposable PostgreSQL test database"
)


@pytest.fixture
def workflow():
    engine = create_engine(normalize_database_url(database_url))
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    owner, tester, other = [uuid4() for _ in range(3)]
    with factory.begin() as db:
        for uid in (owner, tester, other):
            upsert_profile(
                db,
                user_id=uid,
                email=f"{uid}@example.test",
                payload=ProfileUpsert(username=uid.hex, display_name="Test member"),
                signup_credit_grant=24,
                public_beta_enabled=True,
                public_beta_max_users=100000,
            )
        campaign = launch_campaign(
            db,
            owner_id=owner,
            payload=CampaignLaunch(
                campaign={**campaign_payload(), "slug": "race-" + owner.hex, "target_testers": 1},
                contract=contract_payload(),
            ),
        )
        assignments = [
            apply_to_campaign(db, campaign_id=campaign.id, tester_id=uid, payload=AssignmentApply())
            for uid in (tester, other)
        ]
    yield factory, owner, tester, assignments
    engine.dispose()


def race(factory, actions):
    barrier = Barrier(2)

    def attempt(index):
        with factory() as db:
            db.execute(text("SET LOCAL lock_timeout = '5s'"))
            barrier.wait(timeout=10)
            try:
                actions[index](db)
                db.commit()
                return "success"
            except DomainError as error:
                db.rollback()
                assert error.status_code == 409
                return "conflict"

    with ThreadPoolExecutor(max_workers=2) as executor:
        return list(executor.map(attempt, range(2)))


def prepare_submission(factory, owner, tester, assignment):
    with factory.begin() as db:
        accept_assignment(db, assignment_id=assignment.id, owner_id=owner)
        start_assignment(db, assignment_id=assignment.id, tester_id=tester)
        tasks = db.scalars(
            select(ContractTask)
            .join(Contract)
            .where(Contract.campaign_id == assignment.campaign_id)
        ).all()
        submission = create_submission(
            db,
            assignment_id=assignment.id,
            tester_id=tester,
            payload=SubmissionCreate(
                summary="Completed every task and recorded the resulting behavior.",
                items=[
                    {"task_id": task.id, "kind": "note", "note": "Observed the required behavior."}
                    for task in tasks
                ],
            ),
        )
        submission.submitted_at = datetime.now(UTC) - timedelta(hours=73)
    return submission


def test_parallel_acceptance_cannot_overfill_campaign(workflow):
    factory, owner, _, assignments = workflow
    actions = [
        lambda db, record=record: accept_assignment(db, assignment_id=record.id, owner_id=owner)
        for record in assignments
    ]
    assert sorted(race(factory, actions)) == ["conflict", "success"]
    with factory() as db:
        assert (
            db.scalar(
                select(func.count())
                .select_from(Assignment)
                .where(
                    Assignment.campaign_id == assignments[0].campaign_id,
                    Assignment.status == AssignmentStatus.ACCEPTED,
                )
            )
            == 1
        )


def test_parallel_approvals_award_only_once(workflow):
    factory, owner, tester, assignments = workflow
    submission = prepare_submission(factory, owner, tester, assignments[0])

    def approve(db):
        create_review(
            db,
            submission_id=submission.id,
            reviewer_id=owner,
            payload=ReviewCreate(decision="approved", notes="All agreed work was completed."),
        )

    assert sorted(race(factory, [approve, approve])) == ["conflict", "success"]
    with factory() as db:
        assert (
            db.scalar(
                select(func.count())
                .select_from(CreditLedgerEntry)
                .where(
                    CreditLedgerEntry.idempotency_key == f"assignment:{assignments[0].id}:reward"
                )
            )
            == 1
        )


def test_owner_review_and_overdue_escalation_have_one_winner(workflow):
    factory, owner, tester, assignments = workflow
    submission = prepare_submission(factory, owner, tester, assignments[0])

    def approve(db):
        create_review(
            db,
            submission_id=submission.id,
            reviewer_id=owner,
            payload=ReviewCreate(decision="approved", notes="All agreed work was completed."),
        )

    def escalate(db):
        open_dispute(
            db,
            assignment_id=assignments[0].id,
            opened_by=tester,
            payload=DisputeCreate(
                submission_id=submission.id, reason="The review deadline passed without a decision."
            ),
        )

    assert sorted(race(factory, [approve, escalate])) == ["conflict", "success"]
