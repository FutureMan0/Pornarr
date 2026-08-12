"""Conservative conversion of matching RSS releases into automatic requests."""

from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from pornarr_core.filters import FilterAction
from pornarr_core.quality import QualityProfile as CoreQualityProfile
from pornarr_core.quality import QualityVerdict, ReleaseCandidate, decide_quality
from pornarr_db.models.entities import Performer, Studio
from pornarr_db.models.monitor import Monitor, MonitorKind
from pornarr_db.models.quality import QualityProfile, QualityProfileItem
from pornarr_db.models.release import ReleaseCache
from pornarr_db.models.request import Request, RequestHistory, RequestStatus
from pornarr_db.release_cache import normalize_release_title
from pornarr_db.release_filters import release_filter_decision
from pornarr_db.session import session_scope
from pornarr_shared.jobs import job

AUTOMATIC_MONITOR_PRIORITY = 60
EXACT_MATCH_SCORE = 100.0


def _known_names(entity: Performer | Studio) -> frozenset[str]:
    aliases = entity.metadata_json.get("aliases", [])
    values = [entity.normalized_name]
    if isinstance(aliases, list):
        values.extend(alias for alias in aliases if isinstance(alias, str))
    return frozenset(normalize_release_title(value) for value in values if value.strip())


def _contains_normalized_phrase(title: str, phrase: str) -> bool:
    return f" {phrase} " in f" {title} "


async def monitor_score(session: AsyncSession, monitor: Monitor, release: ReleaseCache) -> float:
    """Score exact normalized monitor targets; never fuzzy-match automatic work."""

    title = release.normalized_title
    if monitor.kind is MonitorKind.QUERY:
        return (
            EXACT_MATCH_SCORE
            if monitor.normalized_query
            and _contains_normalized_phrase(title, monitor.normalized_query)
            else 0
        )
    entity = await session.get(
        Performer if monitor.kind is MonitorKind.PERFORMER else Studio,
        monitor.performer_id if monitor.kind is MonitorKind.PERFORMER else monitor.studio_id,
    )
    if entity is None:
        return 0
    return (
        EXACT_MATCH_SCORE
        if any(_contains_normalized_phrase(title, name) for name in _known_names(entity))
        else 0
    )


def _quality_item(
    release: ReleaseCache, items: list[QualityProfileItem]
) -> QualityProfileItem | None:
    return next(
        (
            item
            for item in sorted(items, key=lambda item: item.quality_definition.weight, reverse=True)
            if _contains_normalized_phrase(
                release.normalized_title, normalize_release_title(item.quality_definition.name)
            )
        ),
        None,
    )


async def _quality_permits_release(
    session: AsyncSession, monitor: Monitor, release: ReleaseCache
) -> bool:
    profile = await session.scalar(
        select(QualityProfile)
        .options(
            selectinload(QualityProfile.cutoff_quality),
            selectinload(QualityProfile.items).selectinload(QualityProfileItem.quality_definition),
        )
        .where(QualityProfile.id == monitor.quality_profile_id)
    )
    if profile is None:
        return False
    item = _quality_item(release, profile.items)
    if item is None:
        return False
    decision = decide_quality(
        ReleaseCandidate(
            quality=item.quality_definition.name,
            quality_rank=item.quality_definition.weight,
        ),
        CoreQualityProfile(
            allowed_qualities=frozenset(item.quality_definition.name for item in profile.items),
            cutoff_quality_rank=profile.cutoff_quality.weight,
            minimum_custom_format_score=profile.minimum_custom_format_score,
        ),
        existing=None,
    )
    return decision.verdict is QualityVerdict.GRAB


async def match_release(session: AsyncSession, release: ReleaseCache) -> int:
    """Create at most one automatic request per user for a cached release."""

    monitors = list(await session.scalars(select(Monitor).where(Monitor.enabled.is_(True))))
    candidates: dict[UUID, list[Monitor]] = defaultdict(list)
    matched_at = datetime.now(UTC)
    for monitor in monitors:
        if await monitor_score(session, monitor, release) >= monitor.minimum_score:
            monitor.last_match_at = matched_at
            candidates[monitor.user_id].append(monitor)

    created = 0
    for user_id, user_monitors in candidates.items():
        if await session.scalar(
            select(Request.id)
            .where(
                Request.user_id == user_id,
                Request.selected_release_guid == release.guid,
            )
            .limit(1)
        ):
            continue
        if (
            await release_filter_decision(session, user_id, release)
        ).action is not FilterAction.ALLOW:
            continue
        if not await _any_quality_permits_release(session, user_monitors, release):
            continue
        request = Request(
            user_id=user_id,
            query=release.title,
            selected_release_guid=release.guid,
            status=RequestStatus.QUEUED,
            priority=AUTOMATIC_MONITOR_PRIORITY,
            is_automatic=True,
        )
        session.add(request)
        await session.flush()
        session.add(RequestHistory(request_id=request.id, status=RequestStatus.QUEUED))
        created += 1
    return created


async def _any_quality_permits_release(
    session: AsyncSession, monitors: list[Monitor], release: ReleaseCache
) -> bool:
    for monitor in monitors:
        if await _quality_permits_release(session, monitor, release):
            return True
    return False


async def monitor_match(context: dict[str, Any], indexer_id: str, guids: list[str]) -> int:
    """Match one RSS batch after its cache rows have been committed."""

    async with session_scope() as session:
        releases = list(
            await session.scalars(
                select(ReleaseCache).where(
                    ReleaseCache.indexer_id == UUID(indexer_id), ReleaseCache.guid.in_(guids)
                )
            )
        )
        created = 0
        for release in releases:
            created += await match_release(session, release)
        return created


MONITOR_MATCH_JOB = job(monitor_match)
