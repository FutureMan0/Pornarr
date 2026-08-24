"""Monitor backlog-search scheduling and execution."""

from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import cast
from uuid import UUID, uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from pornarr_db.base import Base
from pornarr_db.models.monitor import Monitor, MonitorKind
from pornarr_db.models.release import ReleaseCache
from pornarr_integrations.indexers import Release
from pornarr_worker.jobs import backlog_search as backlog_search_job
from pornarr_worker.jobs.backlog_search import (
    backlog_slot,
    eligible_for_backlog_search,
)
from pornarr_worker.search import SearchResultCallback, SearchTarget


@pytest.fixture
async def session():
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    try:
        async with AsyncSession(engine, expire_on_commit=False) as database_session:
            yield database_session
    finally:
        await engine.dispose()


@dataclass
class MonitorTiming:
    enabled: bool
    created_at: datetime


def test_backlog_slots_are_stable_and_new_monitors_are_ineligible() -> None:
    first = UUID("00000000-0000-0001-0000-000000000000")
    second = UUID("00000000-0000-0002-0000-000000000000")
    now = datetime(2026, 8, 12, tzinfo=UTC)

    assert backlog_slot(first) == 1
    assert backlog_slot(second) == 2
    assert not eligible_for_backlog_search(
        MonitorTiming(enabled=True, created_at=now - timedelta(hours=23)), now
    )
    assert eligible_for_backlog_search(
        MonitorTiming(enabled=True, created_at=now - timedelta(days=1)), now
    )


async def test_dispatch_queues_only_the_monitor_due_in_its_daily_slot(
    session: AsyncSession, monkeypatch
) -> None:
    due_id = UUID("00000000-0000-002a-0000-000000000000")
    due = Monitor(
        id=due_id,
        user_id=uuid4(),
        kind=MonitorKind.QUERY,
        query="Example Performer",
        normalized_query="example performer",
        quality_profile_id=uuid4(),
    )
    later = Monitor(
        id=UUID("00000000-0000-002b-0000-000000000000"),
        user_id=uuid4(),
        kind=MonitorKind.QUERY,
        query="Another Performer",
        normalized_query="another performer",
        quality_profile_id=uuid4(),
    )
    session.add_all((due, later))
    await session.flush()
    now = datetime(2026, 8, 12, 0, 42, tzinfo=UTC)
    due.created_at = now - timedelta(days=2)
    later.created_at = now - timedelta(days=2)
    calls: list[tuple[str, tuple[object, ...], dict[str, object]]] = []

    @asynccontextmanager
    async def scope():
        yield session

    async def enqueue_once(
        _redis: object, function: str, *args: object, **kwargs: object
    ) -> object:
        calls.append((function, args, kwargs))
        return object()

    monkeypatch.setattr(backlog_search_job, "session_scope", scope)
    monkeypatch.setattr(backlog_search_job, "enqueue_once", enqueue_once)

    queued = await backlog_search_job.dispatch_backlog_searches({"redis": object()}, now=now)

    assert queued == 1
    assert calls == [
        (
            "backlog_search",
            (str(due_id), "daily:2026-08-12"),
            {"queue": "pornarr:indexer"},
        )
    ]


async def test_backlog_search_uses_a_fresh_monitor_query_and_matches_results(
    session: AsyncSession, monkeypatch
) -> None:
    monitor = Monitor(
        user_id=uuid4(),
        kind=MonitorKind.QUERY,
        query="Example Performer",
        normalized_query="example performer",
        quality_profile_id=uuid4(),
    )
    indexer_id = uuid4()
    release = ReleaseCache(
        indexer_id=indexer_id,
        guid="release-1",
        title="Example Performer WEB 1080p",
        normalized_title="example performer web 1080p",
        details_url=None,
        download_url=None,
        published_at=None,
        size=1,
        categories=[],
        groups=[],
        raw_payload={},
        expires_at=datetime.now(UTC) + timedelta(days=1),
    )
    session.add_all((monitor, release))
    await session.flush()
    monitor.created_at = datetime.now(UTC) - timedelta(days=2)
    seen: dict[str, object] = {}

    @asynccontextmanager
    async def scope():
        yield session

    async def configured_targets(_: object) -> list[SearchTarget]:
        return [
            SearchTarget(
                id=str(indexer_id),
                base_url="https://indexer.example",
                api_key="secret",
                adapter=None,
            )
        ]

    async def record_search_outcome(*_: object) -> None:
        return None

    async def run_search(_: object, **kwargs: object) -> None:
        seen["query"] = kwargs["query"]
        callback = cast(SearchResultCallback, kwargs["on_result"])
        await callback(
            str(indexer_id),
            "completed",
            [Release("release-1", release.title, None, None, None, None, ())],
            None,
        )

    async def match_release(_: AsyncSession, matched: ReleaseCache) -> int:
        seen["release"] = matched.guid
        return 1

    monkeypatch.setattr(backlog_search_job, "session_scope", scope)
    monkeypatch.setattr(backlog_search_job, "configured_targets", configured_targets)
    monkeypatch.setattr(backlog_search_job, "record_search_outcome", record_search_outcome)
    monkeypatch.setattr(backlog_search_job, "run_search", run_search)
    monkeypatch.setattr(backlog_search_job, "match_release", match_release)

    created = await backlog_search_job.backlog_search(
        {"redis": object()}, str(monitor.id), "daily:2026-08-12"
    )

    assert created == 1
    assert seen == {"query": "Example Performer", "release": "release-1"}
