"""Conservative monitor-to-request matching."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from pornarr_db.base import Base
from pornarr_db.models.entities import Performer, Studio
from pornarr_db.models.filters import (
    ContentFilterProfile,
    ContentFilterRule,
    FilterAction,
    FilterProfileScope,
    FilterRuleKind,
)
from pornarr_db.models.monitor import Monitor, MonitorKind
from pornarr_db.models.quality import QualityDefinition, QualityProfile, QualityProfileItem
from pornarr_db.models.release import ReleaseCache
from pornarr_db.models.request import Request, RequestStatus
from pornarr_db.models.user import User
from pornarr_worker.jobs.monitor_match import AUTOMATIC_MONITOR_PRIORITY, match_release


@pytest.fixture
async def session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    try:
        async with AsyncSession(engine, expire_on_commit=False) as database_session:
            yield database_session
    finally:
        await engine.dispose()


async def _profile(session: AsyncSession) -> QualityProfile:
    quality = QualityDefinition(
        name="WEB 1080p",
        resolution="1080p",
        source="web",
        weight=20,
        minimum_size_mb_per_minute=1,
        maximum_size_mb_per_minute=100,
    )
    profile = QualityProfile(name="Monitor profile", cutoff_quality=quality)
    profile.items = [QualityProfileItem(quality_definition=quality, position=0)]
    session.add(profile)
    await session.flush()
    return profile


async def _profile_with_quality(session: AsyncSession, name: str) -> QualityProfile:
    quality = QualityDefinition(
        name=name,
        resolution=name,
        source="web",
        weight=20,
        minimum_size_mb_per_minute=1,
        maximum_size_mb_per_minute=100,
    )
    profile = QualityProfile(name=f"{name} profile", cutoff_quality=quality)
    profile.items = [QualityProfileItem(quality_definition=quality, position=0)]
    session.add(profile)
    await session.flush()
    return profile


def _release() -> ReleaseCache:
    return ReleaseCache(
        indexer_id=uuid4(),
        guid="release-1",
        title="Alicia Example 2024 WEB 1080p",
        normalized_title="alicia example 2024 web 1080p",
        details_url=None,
        download_url=None,
        published_at=None,
        size=1,
        categories=[],
        groups=[],
        raw_payload={},
        expires_at=datetime.now(UTC),
    )


async def test_alias_and_query_matches_create_one_automatic_request(
    session: AsyncSession,
) -> None:
    user = User(username="owner", password_hash="hash")
    performer = Performer(
        name="Alice Example",
        normalized_name="alice example",
        metadata_json={"aliases": ["Alicia Example"]},
    )
    session.add_all((user, performer))
    profile = await _profile(session)
    session.add_all(
        (
            Monitor(
                user_id=user.id,
                kind=MonitorKind.PERFORMER,
                performer_id=performer.id,
                quality_profile_id=profile.id,
            ),
            Monitor(
                user_id=user.id,
                kind=MonitorKind.QUERY,
                query="Alicia Example",
                normalized_query="alicia example",
                quality_profile_id=profile.id,
            ),
            Request(user_id=user.id, query="Manual", status=RequestStatus.QUEUED),
        )
    )
    release = _release()
    session.add(release)
    await session.flush()

    assert await match_release(session, release) == 1

    requests = list(await session.scalars(select(Request).order_by(Request.query)))
    automatic = next(request for request in requests if request.is_automatic)
    manual = next(request for request in requests if not request.is_automatic)
    assert automatic.selected_release_guid == release.guid
    assert automatic.priority == AUTOMATIC_MONITOR_PRIORITY
    assert automatic.priority < manual.priority
    assert manual.priority == 80
    monitors = list(await session.scalars(select(Monitor)))
    assert all(monitor.last_match_at is not None for monitor in monitors)


async def test_studio_alias_matches_a_release(session: AsyncSession) -> None:
    user = User(username="owner", password_hash="hash")
    studio = Studio(
        name="Example Studio",
        normalized_name="example studio",
        metadata_json={"aliases": ["Example Label"]},
    )
    session.add_all((user, studio))
    profile = await _profile(session)
    release = _release()
    release.title = "Example Label presents WEB 1080p"
    release.normalized_title = "example label presents web 1080p"
    session.add_all(
        (
            Monitor(
                user_id=user.id,
                kind=MonitorKind.STUDIO,
                studio_id=studio.id,
                quality_profile_id=profile.id,
            ),
            release,
        )
    )
    await session.flush()

    assert await match_release(session, release) == 1
    request = await session.scalar(select(Request))
    assert request is not None and request.is_automatic is True


async def test_match_respects_minimum_score_quality_and_filters(session: AsyncSession) -> None:
    user = User(username="owner", password_hash="hash")
    session.add(user)
    profile = await _profile(session)
    release = _release()
    session.add_all(
        (
            Monitor(
                user_id=user.id,
                kind=MonitorKind.QUERY,
                query="Alicia Example",
                normalized_query="alicia example",
                quality_profile_id=profile.id,
                minimum_score=101,
            ),
            release,
        )
    )
    await session.flush()

    assert await match_release(session, release) == 0
    assert await session.scalar(select(Request)) is None

    monitor = await session.scalar(select(Monitor))
    assert monitor is not None
    monitor.minimum_score = 0
    filter_profile = ContentFilterProfile(scope=FilterProfileScope.GLOBAL)
    filter_profile.rules = [
        ContentFilterRule(
            kind=FilterRuleKind.TERM,
            pattern="Alicia",
            action=FilterAction.REJECT,
            enabled=True,
        )
    ]
    session.add(filter_profile)
    await session.flush()

    assert await match_release(session, release) == 0
    assert await session.scalar(select(Request)) is None


async def test_match_rejects_a_release_outside_the_monitor_quality_profile(
    session: AsyncSession,
) -> None:
    user = User(username="owner", password_hash="hash")
    session.add(user)
    profile = await _profile_with_quality(session, "WEB 720p")
    release = _release()
    session.add_all(
        (
            Monitor(
                user_id=user.id,
                kind=MonitorKind.QUERY,
                query="Alicia Example",
                normalized_query="alicia example",
                quality_profile_id=profile.id,
            ),
            release,
        )
    )
    await session.flush()

    assert await match_release(session, release) == 0
    assert await session.scalar(select(Request)) is None
