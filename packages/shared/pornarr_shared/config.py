"""Application configuration.

Only bootstrap values live in the environment. Indexers, download clients,
metadata providers, quality profiles and content filters are configured in the
UI and stored encrypted in the database — see docs/adr/0002-indexer-protocols.md
and docs/adr/0003-download-clients.md for why.
"""

from __future__ import annotations

from functools import lru_cache
from ipaddress import IPv4Network, IPv6Network, ip_network
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, ValidationError, ValidationInfo, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from pornarr_shared.errors import ConfigurationError

MINIMUM_SECRET_LENGTH = 32


def _split_addresses(value: str) -> tuple[str, ...]:
    return tuple(entry.strip() for entry in value.split(",") if entry.strip())


class Settings(BaseSettings):
    """Bootstrap configuration, read once at startup.

    Secrets are `SecretStr` so that logging a settings object, or letting one
    reach a traceback, cannot leak them.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_ignore_empty=True,
        extra="ignore",
        frozen=True,
    )

    app_env: Literal["development", "test", "production"] = "development"

    # Authentication cookies carry `Secure` unless a deployment says otherwise.
    #
    # The documented deployment terminates TLS in a reverse proxy, so the
    # default has to be the TLS one: a session cookie without `Secure` is sent
    # in the clear the first time anything reaches the instance over http://,
    # and that is one request from a full account takeover. The opt-out is for
    # local development over plain HTTP, where a browser will not store a
    # `Secure` cookie at all; it has to be written into the environment by hand,
    # and `_secure_cookies_are_mandatory_in_production` refuses it outright when
    # APP_ENV=production, so the opt-out cannot be reached by accident.
    session_cookie_secure: bool = True

    # Addresses whose `X-Forwarded-For` this instance may believe, as a
    # comma-separated list of addresses or CIDR blocks. Empty means believe
    # nobody, which is the only safe default: any caller can write that header,
    # so an instance that trusts it unconditionally lets an attacker put every
    # login attempt in a different rate-limit bucket. Set it to the address the
    # reverse proxy reaches the API from -- see docs/operations/deployment.md.
    trusted_proxies: str = ""

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
    user_event_retention_days: int | None = Field(default=365, ge=1)
    quarantine_retention_days: int = Field(default=30, ge=1)

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

    # Household-scope switches. Defaults keep a fresh server behaving exactly as
    # it did before these existed: one shared library, pooled search, and social
    # activity without attribution.
    private_libraries: bool = False
    pooled_search: bool = True
    anonymous_social: bool = True

    # Feed tuning, mirrored by the per-user toggles the design shows.
    recommendation_use_ratings: bool = True
    recommendation_include_friend_picks: bool = True
    recommendation_hide_finished: bool = True
    recommendation_include_shorts: bool = True

    recommendation_tag_weight: float = Field(default=0.30, ge=0)
    recommendation_performer_weight: float = Field(default=0.25, ge=0)
    recommendation_studio_weight: float = Field(default=0.15, ge=0)
    recommendation_quality_weight: float = Field(default=0.10, ge=0)
    recommendation_recency_weight: float = Field(default=0.10, ge=0)
    recommendation_popularity_weight: float = Field(default=0.10, ge=0)
    request_max_active_per_user: int = Field(default=10, ge=0)
    request_search_max_age_days: int = Field(default=90, ge=1)
    rss_sync_interval_minutes: int = Field(default=15, ge=1, le=60)

    oidc_allow_private_issuers: bool = False

    log_level: Literal["debug", "info", "warning", "error"] = "info"
    metrics_enabled: bool = False

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

    @field_validator("session_cookie_secure")
    @classmethod
    def _secure_cookies_are_mandatory_in_production(cls, value: bool, info: ValidationInfo) -> bool:
        if not value and info.data.get("app_env") == "production":
            message = (
                "SESSION_COOKIE_SECURE=false is refused when APP_ENV=production. "
                "It exists for local development over plain HTTP; a production "
                "deployment terminates TLS in a reverse proxy and must mark the "
                "session cookie Secure."
            )
            raise ValueError(message)
        return value

    @field_validator("trusted_proxies")
    @classmethod
    def _trusted_proxies_are_addresses(cls, value: str) -> str:
        for entry in _split_addresses(value):
            try:
                ip_network(entry, strict=False)
            except ValueError as exc:
                message = (
                    f"TRUSTED_PROXIES entry {entry!r} is not an address or CIDR block. "
                    "Write a comma-separated list, e.g. 172.18.0.0/16,10.0.0.5"
                )
                raise ValueError(message) from exc
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

    @field_validator("rss_sync_interval_minutes")
    @classmethod
    def _rss_sync_interval_divides_an_hour(cls, value: int) -> int:
        if 60 % value:
            raise ValueError("RSS_SYNC_INTERVAL_MINUTES must divide 60")
        return value

    @property
    def trusted_proxy_networks(self) -> tuple[IPv4Network | IPv6Network, ...]:
        """`trusted_proxies`, parsed. A bare address becomes a single-host block."""
        return tuple(
            ip_network(entry, strict=False) for entry in _split_addresses(self.trusted_proxies)
        )

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

    @property
    def data_directories(self) -> tuple[Path, ...]:
        """Every directory the product writes into below the data mount."""
        return (
            self.torrents_path,
            self.usenet_path,
            self.library_path,
            self.quarantine_path,
            self.thumbnail_path,
            self.transcode_path,
        )

    def ensure_data_directories(self) -> None:
        """Create the data tree so a first start is not reported as unhealthy.

        Only the mount itself is the operator's to provide. Nothing else
        created these, so a fresh install answered every health check with
        "data path is unavailable" until someone made the directories by hand.
        """
        for directory in self.data_directories:
            directory.mkdir(parents=True, exist_ok=True)


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
