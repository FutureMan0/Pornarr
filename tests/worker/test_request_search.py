"""Request-monitoring schedules and quality-first candidate selection."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from pornarr_db.base import Base
from pornarr_db.models.download import DownloadJob
from pornarr_db.models.download_client import DownloadClient
from pornarr_db.models.indexer import Indexer, IndexerStats
from pornarr_db.models.quality import QualityDefinition, QualityProfile, QualityProfileItem
from pornarr_db.models.release import ReleaseCache
from pornarr_db.models.request import Request, RequestStatus
from pornarr_db.models.user import User
from pornarr_db.requests import transition_request
from pornarr_db.types import set_cipher
from pornarr_shared.crypto import CredentialCipher
from pornarr_worker.jobs import request_search as request_search_job
from pornarr_worker.jobs.request_search import _best_release, search_retry_delay

SECRET = "0123456789abcdef0123456789abcdef"


@pytest.fixture(autouse=True)
def _cipher() -> Iterator[None]:
    set_cipher(CredentialCipher(SECRET))
    yield
    set_cipher(None)


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


def test_search_retry_delays_lengthen_and_cap() -> None:
    assert search_retry_delay(1) == timedelta(hours=1)
    assert search_retry_delay(2) == timedelta(hours=2)
    assert search_retry_delay(4) == timedelta(hours=8)
    assert search_retry_delay(20) == timedelta(days=7)


class RecordingTorrentAdapter:
    def __init__(self) -> None:
        self.magnets: list[str] = []

    async def add_magnet(self, **kwargs: object) -> None:
        magnet = kwargs["magnet"]
        assert isinstance(magnet, str)
        self.magnets.append(magnet)


def _profile() -> QualityProfile:
    quality = QualityDefinition(
        name="WEB 1080p",
        resolution="1080p",
        source="web",
        weight=20,
        minimum_size_mb_per_minute=1,
        maximum_size_mb_per_minute=100,
    )
    cutoff = QualityDefinition(
        name="WEB 2160p",
        resolution="2160p",
        source="web",
        weight=30,
        minimum_size_mb_per_minute=1,
        maximum_size_mb_per_minute=100,
    )
    profile = QualityProfile(name="Default", cutoff_quality=cutoff, is_default=True)
    profile.items = [QualityProfileItem(quality_definition=quality, position=0)]
    return profile


async def test_best_release_uses_the_default_quality_decision_not_indexer_order(
    session: AsyncSession,
) -> None:
    low = QualityDefinition(
        name="WEB 720p",
        resolution="720p",
        source="web",
        weight=10,
        minimum_size_mb_per_minute=1,
        maximum_size_mb_per_minute=100,
    )
    high = QualityDefinition(
        name="WEB 1080p",
        resolution="1080p",
        source="web",
        weight=20,
        minimum_size_mb_per_minute=1,
        maximum_size_mb_per_minute=100,
    )
    cutoff = QualityDefinition(
        name="WEB 2160p",
        resolution="2160p",
        source="web",
        weight=30,
        minimum_size_mb_per_minute=1,
        maximum_size_mb_per_minute=100,
    )
    profile = QualityProfile(name="Default", cutoff_quality=cutoff, is_default=True)
    profile.items = [
        QualityProfileItem(quality_definition=low, position=0),
        QualityProfileItem(quality_definition=high, position=1),
    ]
    indexer = Indexer(
        name="Indexer",
        protocol="torrent",
        implementation="torznab",
        base_url="https://indexer.example",
        api_key="secret",
    )
    session.add_all((profile, indexer))
    await session.flush()
    session.add(IndexerStats(indexer_id=indexer.id))
    for guid, title in (("low", "Example WEB 720p"), ("high", "Example WEB 1080p")):
        session.add(
            ReleaseCache(
                indexer_id=indexer.id,
                guid=guid,
                title=title,
                normalized_title="example web " + ("720p" if guid == "low" else "1080p"),
                details_url=None,
                download_url=None,
                published_at=None,
                size=1,
                categories=[],
                info_hash="0123456789abcdef0123456789abcdef01234567",
                magnet_url="magnet:?xt=urn:btih:0123456789abcdef0123456789abcdef01234567",
                groups=[],
                raw_payload={},
                expires_at=datetime.now(UTC) + timedelta(days=1),
            )
        )
    await session.flush()

    selected = await _best_release(session, "Example")

    assert selected is not None
    assert selected.guid == "high"


async def test_monitoring_can_end_as_an_explicit_not_found_request(session: AsyncSession) -> None:
    user = User(username="alice", password_hash="hash")
    session.add(user)
    await session.flush()
    request = Request(user_id=user.id, query="Unreleased", status=RequestStatus.MONITORING)
    session.add(request)
    await session.flush()

    await transition_request(session, request, RequestStatus.NOT_FOUND)

    assert request.status is RequestStatus.NOT_FOUND


async def test_request_search_grabs_a_later_quality_candidate(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    user = User(username="alice", password_hash="hash")
    session.add(user)
    await session.flush()
    request = Request(
        user_id=user.id,
        query="Example",
        status=RequestStatus.SEARCHING,
        next_search_at=datetime.now(UTC),
        search_expires_at=datetime.now(UTC) + timedelta(days=1),
    )
    indexer = Indexer(
        name="Indexer",
        protocol="torrent",
        implementation="torznab",
        base_url="https://indexer.example",
        api_key="secret",
    )
    client = DownloadClient(
        name="Torrent",
        protocol="torrent",
        implementation="qbittorrent",
        host="client.example",
        port=8080,
        credentials="secret",
        health="healthy",
    )
    session.add_all((request, indexer, client, _profile()))
    await session.flush()
    session.add(IndexerStats(indexer_id=indexer.id))
    session.add(
        ReleaseCache(
            indexer_id=indexer.id,
            guid="release-1",
            title="Example WEB 1080p",
            normalized_title="example web 1080p",
            details_url=None,
            download_url=None,
            published_at=None,
            size=100,
            categories=[],
            info_hash="0123456789abcdef0123456789abcdef01234567",
            magnet_url="magnet:?xt=urn:btih:0123456789abcdef0123456789abcdef01234567",
            groups=[],
            raw_payload={},
            expires_at=datetime.now(UTC) + timedelta(days=1),
        )
    )
    await session.flush()
    adapter = RecordingTorrentAdapter()

    @asynccontextmanager
    async def scope():
        yield session

    async def configured_targets(_: object) -> list[object]:
        return []

    async def run_search(_: object, **__: object) -> None:
        return None

    monkeypatch.setattr(request_search_job, "session_scope", scope)
    monkeypatch.setattr(request_search_job, "configured_targets", configured_targets)
    monkeypatch.setattr(request_search_job, "run_search", run_search)
    monkeypatch.setattr(request_search_job, "SUBMISSION_ADAPTERS", {"qbittorrent": adapter})

    outcome = await request_search_job.request_search({"redis": object()}, str(request.id), 0)

    assert outcome == "queued"
    assert request.status is RequestStatus.QUEUED
    assert request.selected_release_guid == "release-1"
    assert adapter.magnets == ["magnet:?xt=urn:btih:0123456789abcdef0123456789abcdef01234567"]
    assert len(list(await session.scalars(select(DownloadJob)))) == 1
