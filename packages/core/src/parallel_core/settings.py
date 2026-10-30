"""Runtime configuration (environment variables prefixed ``PARALLEL_``; see .env.example)."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[4]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="PARALLEL_", env_file=".env", extra="ignore")

    environment: Literal["development", "test", "production"] = "development"
    database_url: str = "postgresql+psycopg://parallel:parallel@localhost:5432/parallel"
    redis_url: str = "redis://localhost:6379/0"
    secret_key: str = Field(
        default="dev-insecure-change-me-not-for-production",
        description="Signs session tokens and asset URLs; at least 32 characters",
    )
    public_base_url: str = "http://localhost:8000"
    web_origin: str = "http://localhost:5173"

    # Authentication
    auth_mode: Literal["local", "oidc"] = "local"
    oidc_issuer: str | None = None
    oidc_audience: str | None = None
    oidc_jwks_url: str | None = None
    access_token_minutes: int = 12 * 60
    # Open access: requests without a token are served as a limited guest in a shared sandbox organisation, so
    # the site works with no sign-in (for demonstrations and review). Off by default; see docs/deployment.md.
    open_access: bool = False
    open_access_email: str = "guest@parallel-open.example"
    open_access_max_upload_bytes: int = 300 * 1024**2
    open_access_storage_cap_bytes: int = 5 * 1024**3
    open_access_max_active_reconstructions: int = 2

    # Storage
    storage_backend: Literal["local", "s3"] = "local"
    storage_root: Path = REPO_ROOT / "var" / "storage"
    s3_endpoint_url: str | None = None
    s3_bucket: str = "parallel"
    s3_region: str = "us-east-1"
    s3_access_key_id: str | None = None
    s3_secret_access_key: str | None = None
    signed_url_seconds: int = 900

    # Uploads and archives
    max_upload_bytes: int = 4 * 1024**3
    max_archive_total_bytes: int = 8 * 1024**3

    # Jobs and workers
    workspace_root: Path = REPO_ROOT / "var" / "workspaces"
    runtimes_dir: Path = REPO_ROOT / ".runtimes"
    task_always_eager: bool = False
    heartbeat_timeout_s: int = 120
    max_run_attempts: int = 2
    org_concurrent_run_limit: int = 8
    org_monthly_run_quota: int = 5000

    # AI assistant (optional: every modelling and simulation workflow works without it)
    assistant_provider: Literal["anthropic", "gemini", "scripted", "disabled"] = "anthropic"
    anthropic_api_key: str | None = None
    assistant_model: str = "claude-opus-5-5"
    # Gemini (Google AI Studio or any endpoint with the same REST interface). The model is set separately
    # because model identifiers are not shared between providers; see docs/assistant-providers.md.
    gemini_api_key: str | None = None
    gemini_model: str = "gemini-2.5-pro"
    gemini_base_url: str = "https://generativelanguage.googleapis.com"
    assistant_effort: Literal["low", "medium", "high", "xhigh", "max"] = "medium"
    assistant_max_tool_rounds: int = 8


    @model_validator(mode="after")
    def _production_secrets(self) -> Settings:
        if self.environment == "production" and (
            self.secret_key.startswith("dev-insecure") or len(self.secret_key) < 32
        ):
            raise ValueError("PARALLEL_SECRET_KEY must be a random value of at least 32 characters in production")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
