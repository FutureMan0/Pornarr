"""Application configuration.

Only bootstrap values live in the environment. Indexers, download clients,
metadata providers, quality profiles and content filters are configured in the
UI and stored encrypted in the database — see docs/adr/0002-indexer-protocols.md
and docs/adr/0003-download-clients.md for why.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, ValidationError, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from pornarr_shared.errors import ConfigurationError

MINIMUM_SECRET_LENGTH = 32


class Settings(BaseSettings):
    """Bootstrap configuration, read once at startup.

    Secrets are `SecretStr` so that logging a settings object, or letting one
    reach a traceback, cannot leak them.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        frozen=True,
    )

    app_env: Literal["development", "test", "production"] = "development"

    app_secret: SecretStr = Field(
        description="Derives the encryption key for every stored credential. "
        "Generate with `openssl rand -hex 32`."
    )

    database_url: str
    redis_url: str

    data_path: Path = Path("/data")
    backup_path: Path = Path("/backups")
    backup_max_age_hours: int | None = Field(default=None, ge=1)

    base_path: str = ""

    transcode_hwaccel: Literal["auto", "nvenc", "vaapi", "qsv", "none"] = "auto"
    transcode_max_hw_sessions: int | None = None
    transcode_max_sw_sessions: int | None = None
    transcode_max_per_user: int = Field(default=2, ge=1)
    transcode_cleanup_min_age_seconds: int = Field(default=300, ge=1)
    transcode_cache_max_gb: int = Field(default=10, ge=1)

    playback_completion_threshold_percent: int = Field(default=90, ge=1, le=100)
    audit_retention_days: int | None = Field(default=None, ge=1)

    min_free_disk_percent: int = Field(default=15, ge=0, le=99)
    default_daily_download_limit_gb: int = Field(default=10, ge=0)
    default_max_auto_jobs: int = Field(default=2, ge=0)
    default_max_auto_downloads_per_day: int = Field(default=3, ge=0)
    default_auto_downloads_enabled: bool = False
    auto_download_recommendation_weight: float = Field(default=0.30, ge=0)
    auto_download_metadata_weight: float = Field(default=0.15, ge=0)
    auto_download_release_weight: float = Field(default=0.15, ge=0)
    auto_download_indexer_reliability_weight: float = Field(default=0.10, ge=0)
    auto_download_recency_weight: float = Field(default=0.10, ge=0)
    auto_download_size_weight: float = Field(default=0.05, ge=0)
    auto_download_duplicate_risk_weight: float = Field(default=0.10, ge=0)
    auto_download_expected_download_time_weight: float = Field(default=0.05, ge=0)

    oidc_allow_private_issuers: bool = False

    log_level: Literal["debug", "info", "warning", "error"] = "info"

    @field_validator("app_secret")
    @classmethod
    def _secret_is_long_enough(cls, value: SecretStr) -> SecretStr:
        if len(value.get_secret_value()) < MINIMUM_SECRET_LENGTH:
            message = (
                f"APP_SECRET must be at least {MINIMUM_SECRET_LENGTH} characters. "
                "Generate one with: openssl rand -hex 32"
            )
            raise ValueError(message)
        return value

    @field_validator("base_path")
    @classmethod
    def _normalise_base_path(cls, value: str) -> str:
        """Accept `pornarr`, `/pornarr` and `/pornarr/` as the same thing.

        Sub-path deployment is fiddly enough behind a reverse proxy without the
        application also caring which of the three the operator typed.
        """
        stripped = value.strip().strip("/")
        return f"/{stripped}" if stripped else ""

    # Derived paths. Downloads and library must share one filesystem or
    # hardlinking fails; see docs/operations/deployment.md.
    @property
    def torrents_path(self) -> Path:
        return self.data_path / "torrents"

    @property
    def usenet_path(self) -> Path:
        return self.data_path / "usenet"

    @property
    def library_path(self) -> Path:
        return self.data_path / "library"

    @property
    def quarantine_path(self) -> Path:
        return self.data_path / "quarantine"

    @property
    def thumbnail_path(self) -> Path:
        return self.data_path / "thumbnails"

    @property
    def transcode_path(self) -> Path:
        return self.data_path / "transcodes"

    @property
    def transcode_cache_max_bytes(self) -> int:
        return self.transcode_cache_max_gb * 1024**3


def load_settings() -> Settings:
    """Build settings, turning validation failures into a readable message.

    A pydantic `ValidationError` traceback is not something an operator should
    have to read to learn that `APP_SECRET` is missing.
    """
    try:
        return Settings()  # type: ignore[call-arg]  # values come from the environment
    except ValidationError as exc:
        problems = "\n".join(
            f"  {'.'.join(str(p) for p in error['loc']).upper()}: {error['msg']}"
            for error in exc.errors()
        )
        message = f"Configuration is invalid:\n{problems}\n\nSee .env.example for every setting."
        raise ConfigurationError(message) from exc


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Cached accessor. Injected rather than imported at module scope, so tests
    can construct their own settings without touching global state."""
    return load_settings()
