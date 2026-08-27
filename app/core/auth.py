from dataclasses import dataclass
from typing import Annotated
from uuid import UUID

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jwt import PyJWKClient

from app.core.config import Settings, get_settings


@dataclass(frozen=True, slots=True)
class CurrentUser:
    id: UUID
    email: str | None
    role: str


bearer_scheme = HTTPBearer(auto_error=False)


class SupabaseTokenVerifier:
    """Verify Supabase access tokens locally against the project's JWKS endpoint."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._jwks_client: PyJWKClient | None = None

    @property
    def jwks_client(self) -> PyJWKClient:
        if self._jwks_client is None:
            self._jwks_client = PyJWKClient(self.settings.supabase_jwks_url, cache_keys=True)
        return self._jwks_client

    def verify(self, token: str) -> CurrentUser:
        try:
            signing_key = self.jwks_client.get_signing_key_from_jwt(token)
            claims = jwt.decode(
                token,
                signing_key.key,
                algorithms=["RS256", "ES256", "EdDSA"],
                audience=self.settings.supabase_jwt_audience,
                issuer=self.settings.supabase_issuer,
                options={"require": ["exp", "iss", "sub", "aud"]},
            )
            return CurrentUser(
                id=UUID(claims["sub"]),
                email=claims.get("email"),
                role=claims.get("role", "authenticated"),
            )
        except (jwt.PyJWTError, ValueError, KeyError) as exc:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid or expired access token",
                headers={"WWW-Authenticate": "Bearer"},
            ) from exc


def get_token_verifier(
    settings: Annotated[Settings, Depends(get_settings)],
) -> SupabaseTokenVerifier:
    return SupabaseTokenVerifier(settings)


def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
    verifier: Annotated[SupabaseTokenVerifier, Depends(get_token_verifier)],
) -> CurrentUser:
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return verifier.verify(credentials.credentials)


AuthenticatedUser = Annotated[CurrentUser, Depends(get_current_user)]
