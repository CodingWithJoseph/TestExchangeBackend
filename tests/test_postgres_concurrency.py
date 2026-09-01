import os
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.session import normalize_database_url
from app.models import BetaProgramState
from app.schemas.api import ProfileUpsert
from app.services.common import DomainError
from app.services.profiles import upsert_profile

database_url = os.getenv("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not database_url,
    reason="TEST_DATABASE_URL is required for PostgreSQL locking tests",
)


def test_public_beta_seat_claim_is_concurrency_safe() -> None:
    assert database_url is not None
    engine = create_engine(normalize_database_url(database_url), pool_pre_ping=True)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    barrier = Barrier(2)
    suffix = uuid4().hex[:10]

    with factory() as db:
        state = db.get(BetaProgramState, 1)
        assert state is not None
        original_claimed_seats = state.claimed_seats

    def claim_seat(index: int) -> str:
        user_id = uuid4()
        barrier.wait(timeout=10)
        with factory() as db:
            try:
                upsert_profile(
                    db,
                    user_id=user_id,
                    email=f"seat-{suffix}-{index}@example.com",
                    payload=ProfileUpsert(
                        username=f"seat-{suffix}-{index}",
                        display_name=f"Seat claimant {index}",
                    ),
                    signup_credit_grant=0,
                    public_beta_enabled=True,
                    public_beta_max_users=original_claimed_seats + 1,
                )
                db.commit()
                return "accepted"
            except DomainError as error:
                db.rollback()
                assert error.status_code == 409
                return "full"

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(claim_seat, range(2)))

    assert sorted(results) == ["accepted", "full"]
    with factory() as db:
        state = db.get(BetaProgramState, 1)
        assert state is not None
        assert state.claimed_seats == original_claimed_seats + 1
    engine.dispose()
