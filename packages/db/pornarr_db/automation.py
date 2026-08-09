"""Persistence boundary for user automation limits."""

from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from pornarr_db.models.automation import AutomationRule
from pornarr_db.settings import RuntimeSettings


class AutomationLimitError(ValueError):
    """A user selected a value beyond an administrator-set limit."""


class AutomationRuleWrite(BaseModel):
    """The complete user-owned automation policy."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    minimum_score: float = Field(default=0, ge=0)
    daily_download_limit_gb: int = Field(default=10, ge=0)
    max_concurrent_jobs: int = Field(default=2, ge=0)
    max_downloads_per_day: int = Field(default=3, ge=0)
    allowed_qualities: list[str] = Field(default_factory=list)
    blocked_tags: list[str] = Field(default_factory=list)
    blocked_performers: list[str] = Field(default_factory=list)


def _validate_caps(values: AutomationRuleWrite, limits: RuntimeSettings) -> None:
    caps = {
        "daily_download_limit_gb": limits.default_daily_download_limit_gb,
        "max_concurrent_jobs": limits.default_max_auto_jobs,
        "max_downloads_per_day": limits.default_max_auto_downloads_per_day,
    }
    for field, cap in caps.items():
        if getattr(values, field) > cap:
            raise AutomationLimitError(f"{field} exceeds the administrator cap of {cap}")


async def save_automation_rule(
    session: AsyncSession,
    user_id: UUID,
    values: AutomationRuleWrite,
    limits: RuntimeSettings,
) -> AutomationRule:
    """Create or replace one user's rule after enforcing administrator caps."""
    _validate_caps(values, limits)
    rule = await session.get(AutomationRule, user_id)
    if rule is None:
        rule = AutomationRule(user_id=user_id)
        session.add(rule)
    for field, value in values.model_dump().items():
        setattr(rule, field, value)
    return rule
