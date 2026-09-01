from functools import lru_cache
from uuid import UUID

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "TestExchange API"
    app_env: str = "development"
    api_prefix: str = "/api/v1"
    database_url: str = "sqlite:///./testexchange.db"
    supabase_url: str | None = None
    supabase_jwt_audience: str = "authenticated"
    cors_origins: list[str] = ["http://localhost:5173", "http://127.0.0.1:5173"]
    signup_credit_grant: int = Field(default=24, ge=0, le=10000)
    public_beta_enabled: bool = True
    public_beta_max_users: int = Field(default=200, ge=1, le=100000)
    rate_limit_enabled: bool = True
    rate_limit_window_seconds: int = Field(default=60, ge=1, le=3600)
    rate_limit_read_requests: int = Field(default=120, ge=1, le=10000)
    rate_limit_write_requests: int = Field(default=30, ge=1, le=10000)
    moderator_user_ids: list[UUID] = Field(default_factory=list)

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    @field_validator("cors_origins", mode="before")
    @classmethod
    def parse_origins(cls, value: object) -> object:
        if isinstance(value, str) and not value.startswith("["):
            return [origin.strip() for origin in value.split(",") if origin.strip()]
        return value

    @field_validator("moderator_user_ids", mode="before")
    @classmethod
    def parse_moderators(cls, value: object) -> object:
        if isinstance(value, str) and not value.startswith("["):
            return [user_id.strip() for user_id in value.split(",") if user_id.strip()]
        return value

    @model_validator(mode="after")
    def validate_production_safety(self) -> "Settings":
        if self.app_env.lower() not in {"production", "staging"}:
            return self
        problems: list[str] = []
        if not self.database_url.startswith(("postgres://", "postgresql://")):
            problems.append("DATABASE_URL must use PostgreSQL")
        if not self.supabase_url:
            problems.append("SUPABASE_URL is required")
        if any("localhost" in origin or "127.0.0.1" in origin for origin in self.cors_origins):
            problems.append("CORS_ORIGINS cannot contain localhost")
        if not self.moderator_user_ids:
            problems.append("MODERATOR_USER_IDS must contain at least one moderator")
        if problems:
            raise ValueError("Unsafe production configuration: " + "; ".join(problems))
        return self

    @property
    def supabase_issuer(self) -> str:
        if not self.supabase_url:
            raise ValueError("SUPABASE_URL is required for authenticated requests")
        return f"{self.supabase_url.rstrip('/')}/auth/v1"

    @property
    def supabase_jwks_url(self) -> str:
        return f"{self.supabase_issuer}/.well-known/jwks.json"


@lru_cache
def get_settings() -> Settings:
    return Settings()
