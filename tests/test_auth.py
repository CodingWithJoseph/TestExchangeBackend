from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.core.auth import SupabaseTokenVerifier, get_token_verifier
from app.core.config import Settings, get_settings
from app.main import app
from tests.conftest import OWNER_ID, TESTER_ID, auth_headers, create_profile


class FakeJWKClient:
    def __init__(self, public_key) -> None:
        self.public_key = public_key

    def get_signing_key_from_jwt(self, _token: str) -> SimpleNamespace:
        return SimpleNamespace(key=self.public_key)


def build_token(*, private_key, issuer: str, audience: str, subject: str) -> str:
    now = datetime.now(UTC)
    return jwt.encode(
        {
            "sub": subject,
            "email": "tester@example.com",
            "role": "authenticated",
            "iss": issuer,
            "aud": audience,
            "iat": now,
            "exp": now + timedelta(minutes=10),
        },
        private_key,
        algorithm="RS256",
        headers={"kid": "test-key"},
    )


def test_supabase_jwt_is_verified_against_issuer_and_audience() -> None:
    settings = Settings(supabase_url="https://project.supabase.co")
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    verifier = SupabaseTokenVerifier(settings)
    verifier._jwks_client = FakeJWKClient(private_key.public_key())  # type: ignore[assignment]
    subject = str(uuid4())

    token = build_token(
        private_key=private_key,
        issuer=settings.supabase_issuer,
        audience=settings.supabase_jwt_audience,
        subject=subject,
    )
    user = verifier.verify(token)

    assert str(user.id) == subject
    assert user.email == "tester@example.com"
    assert user.role == "authenticated"


def test_wrong_audience_is_rejected() -> None:
    settings = Settings(supabase_url="https://project.supabase.co")
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    verifier = SupabaseTokenVerifier(settings)
    verifier._jwks_client = FakeJWKClient(private_key.public_key())  # type: ignore[assignment]
    token = build_token(
        private_key=private_key,
        issuer=settings.supabase_issuer,
        audience="not-testexchange",
        subject=str(uuid4()),
    )

    with pytest.raises(HTTPException) as error:
        verifier.verify(token)

    assert error.value.status_code == 401


def test_protected_routes_require_a_bearer_token() -> None:
    with TestClient(app) as client:
        response = client.get("/api/v1/me/profile")

    assert response.status_code == 401
    assert response.json()["detail"] == "Authentication required"


def test_token_verifier_is_cached_across_requests() -> None:
    get_token_verifier.cache_clear()
    assert get_token_verifier() is get_token_verifier()
    get_token_verifier.cache_clear()


def test_unsafe_production_configuration_fails_fast() -> None:
    with pytest.raises(ValueError, match="Unsafe production configuration"):
        Settings(app_env="production")


def test_public_beta_enforces_capacity_and_accepts_waitlist(client: TestClient) -> None:
    client.app.dependency_overrides[get_settings] = lambda: Settings(
        public_beta_enabled=True,
        public_beta_max_users=1,
    )
    initial = client.get("/beta/status")
    accepted = client.put(
        "/api/v1/me/profile",
        headers=auth_headers(OWNER_ID),
        json={"username": "first-member", "display_name": "First Member"},
    )

    rejected = client.put(
        "/api/v1/me/profile",
        headers=auth_headers(TESTER_ID),
        json={"username": "over-capacity", "display_name": "Over Capacity"},
    )
    full = client.get("/beta/status")
    waitlisted = client.post(
        "/beta/waitlist",
        json={"email": "  WAITING@Example.com "},
    )
    duplicate = client.post("/beta/waitlist", json={"email": "waiting@example.com"})

    assert initial.json()["remaining_seats"] == 1
    assert accepted.status_code == 200
    assert rejected.status_code == 409
    assert "public beta is full" in rejected.json()["detail"]
    assert full.json()["is_full"] is True
    assert waitlisted.status_code == 201
    assert waitlisted.json()["email"] == "waiting@example.com"
    assert duplicate.json()["id"] == waitlisted.json()["id"]


def test_moderator_can_suspend_and_restore_participant(client: TestClient) -> None:
    create_profile(client, OWNER_ID, "moderator-owner")
    create_profile(client, TESTER_ID, "public-tester")
    client.app.dependency_overrides[get_settings] = lambda: Settings(moderator_user_ids=[OWNER_ID])

    suspended = client.post(
        f"/api/v1/moderation/participants/{TESTER_ID}/suspend",
        headers=auth_headers(OWNER_ID),
        json={"reason": "Repeatedly submitted prohibited rating-exchange campaigns."},
    )
    blocked = client.get("/api/v1/me/profile", headers=auth_headers(TESTER_ID))
    restored = client.post(
        f"/api/v1/moderation/participants/{TESTER_ID}/restore",
        headers=auth_headers(OWNER_ID),
    )
    accessible = client.get("/api/v1/me/profile", headers=auth_headers(TESTER_ID))

    assert suspended.status_code == 200
    assert suspended.json()["is_suspended"] is True
    assert blocked.status_code == 403
    assert restored.json()["is_suspended"] is False
    assert accessible.status_code == 200


def test_readiness_checks_the_application_schema(client: TestClient) -> None:
    response = client.get("/ready")

    assert response.status_code == 200
    assert response.json()["status"] == "ready"
    assert response.headers["X-Request-ID"]
