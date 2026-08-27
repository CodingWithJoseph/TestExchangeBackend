from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.core.auth import SupabaseTokenVerifier
from app.core.config import Settings
from app.main import app


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
