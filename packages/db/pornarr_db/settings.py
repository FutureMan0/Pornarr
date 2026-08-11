"""Typed access to persisted runtime-setting overrides."""

from __future__ import annotations

from typing import Any, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from pornarr_db.models.settings import Setting
from pornarr_shared.config import Settings

_RUNTIME_SETTING_FIELDS = frozenset(
    {
        "transcode_max_hw_sessions",
        "transcode_max_sw_sessions",
        "transcode_max_per_user",
        "playback_completion_threshold_percent",
        "audit_retention_days",
        "min_free_disk_percent",
        "default_daily_download_limit_gb",
        "default_max_auto_jobs",
        "default_max_auto_downloads_per_day",
        "default_auto_downloads_enabled",
        "auto_download_recommendation_weight",
        "auto_download_metadata_weight",
        "auto_download_release_weight",
        "auto_download_indexer_reliability_weight",
        "auto_download_recency_weight",
        "auto_download_size_weight",
        "auto_download_duplicate_risk_weight",
        "auto_download_expected_download_time_weight",
        "log_level",
        "metrics_enabled",
    }
)


class RuntimeSettings(BaseModel):
    """Resolved values with attributes callers can use directly."""

    transcode_max_hw_sessions: int | None
    transcode_max_sw_sessions: int | None
    transcode_max_per_user: int
    playback_completion_threshold_percent: int
    audit_retention_days: int | None
    min_free_disk_percent: int
    default_daily_download_limit_gb: int
    default_max_auto_jobs: int
    default_max_auto_downloads_per_day: int
    default_auto_downloads_enabled: bool
    auto_download_recommendation_weight: float
    auto_download_metadata_weight: float
    auto_download_release_weight: float
    auto_download_indexer_reliability_weight: float
    auto_download_recency_weight: float
    auto_download_size_weight: float
    auto_download_duplicate_risk_weight: float
    auto_download_expected_download_time_weight: float
    log_level: str
    metrics_enabled: bool

    @classmethod
    def from_defaults(cls, defaults: Settings, overrides: dict[str, Any]) -> Self:
        values = {field: getattr(defaults, field) for field in _RUNTIME_SETTING_FIELDS}
        values.update(overrides)
        return cls.model_construct(**values)


class RuntimeSettingsWrite(BaseModel):
    """Partial update validated before values are persisted."""

    model_config = ConfigDict(extra="forbid")

    transcode_max_hw_sessions: int | None = Field(default=None, ge=1)
    transcode_max_sw_sessions: int | None = Field(default=None, ge=1)
    transcode_max_per_user: int | None = Field(default=None, ge=1)
    playback_completion_threshold_percent: int | None = Field(default=None, ge=1, le=100)
    audit_retention_days: int | None = Field(default=None, ge=1)
    min_free_disk_percent: int | None = Field(default=None, ge=0, le=99)
    default_daily_download_limit_gb: int | None = Field(default=None, ge=0)
    default_max_auto_jobs: int | None = Field(default=None, ge=0)
    default_max_auto_downloads_per_day: int | None = Field(default=None, ge=0)
    default_auto_downloads_enabled: bool | None = None
    auto_download_recommendation_weight: float | None = Field(default=None, ge=0)
    auto_download_metadata_weight: float | None = Field(default=None, ge=0)
    auto_download_release_weight: float | None = Field(default=None, ge=0)
    auto_download_indexer_reliability_weight: float | None = Field(default=None, ge=0)
    auto_download_recency_weight: float | None = Field(default=None, ge=0)
    auto_download_size_weight: float | None = Field(default=None, ge=0)
    auto_download_duplicate_risk_weight: float | None = Field(default=None, ge=0)
    auto_download_expected_download_time_weight: float | None = Field(default=None, ge=0)
    log_level: str | None = Field(default=None, pattern="^(debug|info|warning|error)$")
    metrics_enabled: bool | None = None

    @field_validator(
        "transcode_max_per_user",
        "playback_completion_threshold_percent",
        "min_free_disk_percent",
        "default_daily_download_limit_gb",
        "default_max_auto_jobs",
        "default_max_auto_downloads_per_day",
        "default_auto_downloads_enabled",
        "auto_download_recommendation_weight",
        "auto_download_metadata_weight",
        "auto_download_release_weight",
        "auto_download_indexer_reliability_weight",
        "auto_download_recency_weight",
        "auto_download_size_weight",
        "auto_download_duplicate_risk_weight",
        "auto_download_expected_download_time_weight",
        "log_level",
        "metrics_enabled",
    )
    @classmethod
    def value_cannot_be_null(
        cls, value: int | float | bool | str | None
    ) -> int | float | bool | str:
        if value is None:
            raise ValueError("setting cannot be null")
        return value

    @model_validator(mode="after")
    def has_a_value(self) -> Self:
        if not self.model_fields_set:
            raise ValueError("at least one setting must be supplied")
        return self


async def get_runtime_settings(session: AsyncSession, defaults: Settings) -> RuntimeSettings:
    rows = await session.scalars(select(Setting))
    return RuntimeSettings.from_defaults(
        defaults, {row.key: row.value for row in rows if row.key in _RUNTIME_SETTING_FIELDS}
    )


async def update_runtime_settings(
    session: AsyncSession, defaults: Settings, values: RuntimeSettingsWrite
) -> RuntimeSettings:
    rows = {row.key: row for row in await session.scalars(select(Setting))}
    for key, value in values.model_dump(exclude_unset=True).items():
        row = rows.get(key)
        if row is None:
            session.add(Setting(key=key, value=value))
        else:
            row.value = value
    return await get_runtime_settings(session, defaults)
