from dataclasses import dataclass
from functools import lru_cache
from typing import Annotated
from uuid import UUID

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jwt import PyJWKClient
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.db.session import get_db
from app.models import Profile


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


@lru_cache
def get_token_verifier() -> SupabaseTokenVerifier:
    # Keep one PyJWKClient per process so its signing-key cache survives across requests.
    return SupabaseTokenVerifier(get_settings())


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


def get_active_user(
    user: Annotated[CurrentUser, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> CurrentUser:
    profile = db.get(Profile, user.id)
    if profile is not None and profile.is_suspended:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This account is suspended. Contact support if you believe this is an error.",
        )
    return user


AuthenticatedUser = Annotated[CurrentUser, Depends(get_active_user)]


def get_moderator(
    user: AuthenticatedUser,
    settings: Annotated[Settings, Depends(get_settings)],
) -> CurrentUser:
    if user.id not in settings.moderator_user_ids:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Moderator access required",
        )
    return user


ModeratorUser = Annotated[CurrentUser, Depends(get_moderator)]
