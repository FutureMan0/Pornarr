"""Persistence boundary for user automation limits."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from pornarr_core.priorities import RECOMMENDATION_REQUEST_PRIORITY
from pornarr_db.audit import write_audit
from pornarr_db.models.automation import AutomationRule
from pornarr_db.models.download import DownloadHistory, DownloadJob
from pornarr_db.models.request import Request, RequestStatus
from pornarr_db.models.root_folders import RootFolder
from pornarr_db.settings import RuntimeSettings
from pornarr_db.storage import daily_storage_usage, utc_day
from pornarr_shared.audit import AuditSource

GIGABYTE = 1_000_000_000
_ACTIVE_REQUEST_STATUSES = frozenset(
    {
        RequestStatus.SEARCHING,
        RequestStatus.RESULTS_FOUND,
        RequestStatus.QUEUED,
        RequestStatus.DOWNLOADING,
        RequestStatus.PROCESSING,
        RequestStatus.MONITORING,
    }
)


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


@dataclass(frozen=True, slots=True)
class AutomationCandidate:
    """One recommendation that may become an automatic request."""

    user_id: UUID
    release_guid: str
    query: str
    score: float
    size_bytes: int
    quality: str
    metadata_confident: bool
    tags: tuple[str, ...]
    performers: tuple[str, ...]
    breakdown: dict[str, float]


@dataclass(frozen=True, slots=True)
class AutomationDecision:
    """The durable outcome of an automatic-request evaluation."""

    day: date
    created: bool
    reason: str | None = None


async def execute_automation(
    session: AsyncSession, candidate: AutomationCandidate, settings: RuntimeSettings
) -> AutomationDecision:
    """Fail closed, reserve a user's limits, and create a recommendation request.

    Locking the per-user rule serializes concurrent evaluations for that user.
    The daily counter is therefore reserved in the same transaction as the
    request and cannot be oversubscribed by two workers racing for its last slot.
    """

    day = utc_day()
    if not settings.default_auto_downloads_enabled:
        return _refuse(session, candidate, day, "global_kill_switch")
    if not 0 <= candidate.score <= 1:
        return _refuse(session, candidate, day, "invalid_score")
    if candidate.size_bytes <= 0:
        return _refuse(session, candidate, day, "size")

    rule = await session.scalar(
        select(AutomationRule).where(AutomationRule.user_id == candidate.user_id).with_for_update()
    )
    if rule is None or not rule.enabled:
        return _refuse(session, candidate, day, "disabled")
    if not candidate.metadata_confident:
        return _refuse(session, candidate, day, "metadata_confidence")

    score_percent = candidate.score * 100
    if score_percent < rule.minimum_score:
        return _refuse(session, candidate, day, "score_threshold", score_percent)
    if _intersects(candidate.tags, rule.blocked_tags):
        return _refuse(session, candidate, day, "blocked_tag", score_percent)
    if _intersects(candidate.performers, rule.blocked_performers):
        return _refuse(session, candidate, day, "blocked_performer", score_percent)
    if rule.allowed_qualities and candidate.quality not in rule.allowed_qualities:
        return _refuse(session, candidate, day, "quality", score_percent)
    if await _is_duplicate(session, candidate.release_guid):
        return _refuse(session, candidate, day, "duplicate", score_percent)
    if await _active_request_count(session, candidate.user_id) >= rule.max_concurrent_jobs:
        return _refuse(session, candidate, day, "concurrency", score_percent)

    usage = await daily_storage_usage(session, candidate.user_id, now=None)
    if usage.download_count + usage.reserved_download_count >= rule.max_downloads_per_day or (
        usage.downloaded_bytes + usage.reserved_bytes + candidate.size_bytes
        > rule.daily_download_limit_gb * GIGABYTE
    ):
        return _refuse(session, candidate, day, "daily_budget", score_percent)
    if not await _has_free_disk(session, candidate.size_bytes, settings.min_free_disk_percent):
        return _refuse(session, candidate, day, "free_disk", score_percent)

    if usage not in session:
        session.add(usage)
    usage.reserved_bytes += candidate.size_bytes
    usage.reserved_download_count += 1
    request = Request(
        user_id=candidate.user_id,
        query=candidate.query,
        selected_release_guid=candidate.release_guid,
        status=RequestStatus.QUEUED,
        priority=RECOMMENDATION_REQUEST_PRIORITY,
        is_automatic=True,
    )
    session.add(request)
    write_audit(
        session,
        actor_id=candidate.user_id,
        source=AuditSource.AUTOMATION,
        action="automation.requested",
        target=candidate.release_guid,
        context={
            "score": score_percent,
            "size_bytes": candidate.size_bytes,
            "quality": candidate.quality,
            "breakdown": candidate.breakdown,
        },
    )
    await session.flush()
    return AutomationDecision(day=day, created=True)


def _intersects(values: tuple[str, ...], blocked: list[str]) -> bool:
    blocked_values = {value.casefold() for value in blocked}
    return bool({value.casefold() for value in values} & blocked_values)


async def _is_duplicate(session: AsyncSession, release_guid: str) -> bool:
    return (
        await session.scalar(
            select(DownloadJob.id).where(DownloadJob.release_guid == release_guid).limit(1)
        )
        is not None
        or await session.scalar(
            select(DownloadHistory.id).where(DownloadHistory.release_guid == release_guid).limit(1)
        )
        is not None
    )


async def _active_request_count(session: AsyncSession, user_id: UUID) -> int:
    rows = await session.scalars(
        select(Request.id).where(
            Request.user_id == user_id,
            Request.status.in_(_ACTIVE_REQUEST_STATUSES),
        )
    )
    return len(list(rows))


async def _has_free_disk(
    session: AsyncSession, required_bytes: int, minimum_free_percent: int
) -> bool:
    folders = await session.scalars(
        select(RootFolder).where(
            RootFolder.enabled.is_(True),
            RootFolder.total_space_bytes.is_not(None),
        )
    )
    return any(
        (folder.free_space_bytes - required_bytes) * 100
        >= folder.total_space_bytes * minimum_free_percent
        for folder in folders
        if folder.total_space_bytes is not None
    )


def _refuse(
    session: AsyncSession,
    candidate: AutomationCandidate,
    day: date,
    reason: str,
    score_percent: float | None = None,
) -> AutomationDecision:
    write_audit(
        session,
        actor_id=candidate.user_id,
        source=AuditSource.AUTOMATION,
        action="automation.refused",
        target=candidate.release_guid,
        context={
            "reason": reason,
            "score": score_percent if score_percent is not None else candidate.score * 100,
            "size_bytes": candidate.size_bytes,
            "quality": candidate.quality,
            "breakdown": candidate.breakdown,
        },
    )
    return AutomationDecision(day=day, created=False, reason=reason)
