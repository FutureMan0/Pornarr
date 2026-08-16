"""Daily, staggered catalogue searches for existing monitor matches."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, Protocol
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from pornarr_db.models.entities import Performer, Studio
from pornarr_db.models.monitor import Monitor, MonitorKind
from pornarr_db.models.release import ReleaseCache
from pornarr_db.session import session_scope
from pornarr_integrations.indexers import Release
from pornarr_shared.jobs import INDEXER_QUEUE, enqueue_once, job
from pornarr_worker.jobs.monitor_match import match_release
from pornarr_worker.search import configured_targets, record_search_outcome, run_search

MINIMUM_MONITOR_AGE = timedelta(days=1)
MINUTES_PER_DAY = 24 * 60


class BacklogEligibleMonitor(Protocol):
    enabled: bool
    created_at: datetime


def backlog_slot(monitor_id: UUID) -> int:
    """Return the monitor's stable minute of the day for daily backfill work."""

    return int.from_bytes(monitor_id.bytes[:8]) % MINUTES_PER_DAY


def eligible_for_backlog_search(monitor: BacklogEligibleMonitor, now: datetime) -> bool:
    """Avoid backfilling a monitor until its first day has elapsed."""

    cutoff = now - MINIMUM_MONITOR_AGE
    if monitor.created_at.tzinfo is None:
        cutoff = cutoff.replace(tzinfo=None)
    return monitor.enabled and monitor.created_at <= cutoff


async def dispatch_backlog_searches(context: dict[str, Any], *, now: datetime | None = None) -> int:
    """Queue only monitors assigned to the current minute's daily slot."""

    now = now or datetime.now(UTC)
    slot = now.hour * 60 + now.minute
    async with session_scope() as session:
        monitors = list(await session.scalars(select(Monitor).where(Monitor.enabled.is_(True))))
    due = [
        monitor
        for monitor in monitors
        if eligible_for_backlog_search(monitor, now) and backlog_slot(monitor.id) == slot
    ]
    redis = context["redis"]
    run_id = f"daily:{now.date().isoformat()}"
    queued = 0
    for monitor in due:
        queued += int(
            await enqueue_once(
                redis,
                BACKLOG_SEARCH_JOB.name,
                str(monitor.id),
                run_id,
                queue=INDEXER_QUEUE,
            )
            is not None
        )
    return queued


BACKLOG_SEARCH_DISPATCH_JOB = job(dispatch_backlog_searches)


async def backlog_search(context: dict[str, Any], monitor_id: str, run_id: str) -> int:
    """Search fresh indexer results for one mature monitor and match the cache."""

    del run_id
    monitor_uuid = UUID(monitor_id)
    now = datetime.now(UTC)
    async with session_scope() as session:
        monitor = await session.get(Monitor, monitor_uuid)
        if monitor is None or not eligible_for_backlog_search(monitor, now):
            return 0
        query = await _monitor_query(session, monitor)
        if query is None:
            return 0
        user_id = str(monitor.user_id)

    discovered: dict[str, set[str]] = {}
    redis = context["redis"]

    async def record_result(
        indexer_id: str,
        status: str,
        releases: list[Release] | None,
        error: Exception | None,
    ) -> None:
        await record_search_outcome(redis, indexer_id, status, releases, error)
        if status == "completed" and releases:
            discovered[indexer_id] = {release.guid for release in releases}

    await run_search(
        redis,
        search_id=f"monitor-backlog:{monitor_id}",
        user_id=user_id,
        query=query,
        targets=await configured_targets(redis),
        on_result=record_result,
    )

    async with session_scope() as session:
        created = 0
        for indexer_id, guids in discovered.items():
            releases = list(
                await session.scalars(
                    select(ReleaseCache).where(
                        ReleaseCache.indexer_id == UUID(indexer_id),
                        ReleaseCache.guid.in_(guids),
                    )
                )
            )
            for release in releases:
                created += await match_release(session, release)
        return created


async def _monitor_query(session: AsyncSession, monitor: Monitor) -> str | None:
    if monitor.kind is MonitorKind.QUERY:
        return monitor.query
    entity = await session.get(
        Performer if monitor.kind is MonitorKind.PERFORMER else Studio,
        monitor.performer_id if monitor.kind is MonitorKind.PERFORMER else monitor.studio_id,
    )
    return entity.name if entity is not None else None


BACKLOG_SEARCH_JOB = job(backlog_search)
