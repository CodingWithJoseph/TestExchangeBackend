from collections.abc import Generator
from typing import Annotated
from uuid import UUID

import pytest
from fastapi import Header
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.auth import CurrentUser, get_current_user
from app.core.config import get_settings
from app.db.base import Base
from app.db.session import get_db
from app.main import app

OWNER_ID = UUID("10000000-0000-0000-0000-000000000001")
TESTER_ID = UUID("20000000-0000-0000-0000-000000000002")
INTRUDER_ID = UUID("30000000-0000-0000-0000-000000000003")
MODERATOR_ID = UUID("40000000-0000-0000-0000-000000000004")
SECOND_MODERATOR_ID = UUID("50000000-0000-0000-0000-000000000005")


@pytest.fixture
def session_factory() -> Generator[sessionmaker[Session], None, None]:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(dbapi_connection, _connection_record) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    try:
        yield factory
    finally:
        Base.metadata.drop_all(engine)
        engine.dispose()


@pytest.fixture
def client(session_factory: sessionmaker[Session]) -> Generator[TestClient, None, None]:
    def override_db() -> Generator[Session, None, None]:
        with session_factory() as session:
            try:
                yield session
                session.commit()
            except Exception:
                session.rollback()
                raise

    def override_user(
        x_test_user: Annotated[str, Header()],
        x_test_email: Annotated[str | None, Header()] = None,
    ) -> CurrentUser:
        return CurrentUser(
            id=UUID(x_test_user),
            email=x_test_email,
            role="authenticated",
        )

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = override_user
    # Rate limiting is tested directly with an isolated middleware instance. Keep API
    # integration tests independent from one another instead of sharing a process window.
    runtime_settings = get_settings()
    previous_rate_limit = runtime_settings.rate_limit_enabled
    runtime_settings.rate_limit_enabled = False
    try:
        with TestClient(app) as test_client:
            yield test_client
    finally:
        runtime_settings.rate_limit_enabled = previous_rate_limit
        app.dependency_overrides.clear()


def auth_headers(user_id: UUID, email: str | None = None) -> dict[str, str]:
    headers = {"X-Test-User": str(user_id)}
    if email:
        headers["X-Test-Email"] = email
    return headers


def create_profile(client: TestClient, user_id: UUID, username: str) -> dict:
    response = client.put(
        "/api/v1/me/profile",
        headers=auth_headers(user_id, f"{username}@example.com"),
        json={"username": username, "display_name": username.replace("-", " ").title()},
    )
    assert response.status_code == 200, response.text
    return response.json()
