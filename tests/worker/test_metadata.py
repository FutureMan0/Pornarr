"""Metadata confidence cascade and trace logging."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import date
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from pornarr_db.base import Base
from pornarr_db.models.download import ImportTrigger
from pornarr_db.models.metadata_match import MetadataMatchLog
from pornarr_integrations.metadata import MetadataCandidate, MetadataRateLimitError
from pornarr_worker.jobs.metadata import (
    MetadataSubject,
    MetadataTier,
    resolve_metadata_cascade,
)


class Provider:
    name = "provider"
    precedence = 100

    def __init__(
        self,
        *,
        fingerprint: MetadataCandidate | Exception | None = None,
        exact: MetadataCandidate | None = None,
        fuzzy: list[MetadataCandidate] | None = None,
    ) -> None:
        self.fingerprint = fingerprint
        self.exact = exact
        self.fuzzy = fuzzy or []
        self.calls: list[str] = []

    async def find_by_fingerprint(
        self, *, oshash: str | None, perceptual_hash: str | None
    ) -> MetadataCandidate | None:
        self.calls.append("fingerprint")
        if isinstance(self.fingerprint, Exception):
            raise self.fingerprint
        return self.fingerprint

    async def find_by_site_date_title(
        self, *, site: str, release_date: date, title: str
    ) -> MetadataCandidate | None:
        self.calls.append("site_date_title")
        return self.exact

    async def search(self, *, title: str, performers: tuple[str, ...]) -> list[MetadataCandidate]:
        self.calls.append("fuzzy")
        return self.fuzzy


class RateLimitedProvider(Provider):
    name = "limited"

    def __init__(self) -> None:
        super().__init__()
        self.attempts = 0

    async def find_by_fingerprint(
        self, *, oshash: str | None, perceptual_hash: str | None
    ) -> MetadataCandidate | None:
        self.attempts += 1
        if self.attempts == 1:
            raise MetadataRateLimitError(12)
        return None


@pytest.fixture
async def session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as created_session:
        yield created_session
    await engine.dispose()


async def test_fingerprint_match_wins_with_high_confidence_and_a_log(
    session: AsyncSession, tmp_path: Path
) -> None:
    trigger = ImportTrigger(source_path=str(tmp_path / "scene.mkv"))
    session.add(trigger)
    await session.flush()
    provider = Provider(fingerprint=MetadataCandidate(title="Correct Scene", provider_id="scene-1"))

    resolution = await resolve_metadata_cascade(
        session,
        MetadataSubject(source_path="/data/torrents/scene.mkv", title="Scene", oshash="abc"),
        [provider],
        import_trigger_id=trigger.id,
    )

    assert (resolution.candidate.title, resolution.confidence, resolution.tier) == (
        "Correct Scene",
        0.95,
        MetadataTier.FINGERPRINT,
    )
    log = await session.scalar(select(MetadataMatchLog))
    assert log is not None
    assert (log.import_trigger_id, log.provider, log.tier, log.outcome, log.confidence) == (
        trigger.id,
        "provider",
        "fingerprint",
        "matched",
        0.95,
    )


async def test_exact_site_date_title_precedes_fuzzy_matching(session: AsyncSession) -> None:
    provider = Provider(
        exact=MetadataCandidate(
            title="Scene Title", site="Studio", release_date=date(2026, 1, 2), provider_id="exact"
        ),
        fuzzy=[MetadataCandidate(title="Scene Title", performers=("Performer",))],
    )

    resolution = await resolve_metadata_cascade(
        session,
        MetadataSubject(
            source_path="/data/torrents/scene.mkv",
            title="Scene Title",
            site="Studio",
            release_date=date(2026, 1, 2),
            performers=("Performer",),
        ),
        [provider],
    )

    assert (resolution.confidence, resolution.tier, provider.calls) == (
        0.80,
        MetadataTier.SITE_DATE_TITLE,
        ["site_date_title"],
    )


async def test_stashdb_wins_same_tier_conflicts_before_tpdb(session: AsyncSession) -> None:
    stashdb = Provider(
        exact=MetadataCandidate(
            title="Known Scene", site="Example Studio", release_date=date(2024, 2, 14)
        )
    )
    stashdb.name = "stashdb"
    stashdb.precedence = 0
    tpdb = Provider(
        exact=MetadataCandidate(
            title="Conflicting TPDB Scene", site="Example Studio", release_date=date(2024, 2, 14)
        )
    )
    tpdb.name = "tpdb"
    tpdb.precedence = 1

    resolution = await resolve_metadata_cascade(
        session,
        MetadataSubject(
            source_path="/data/torrents/example.mkv",
            title="Known Scene",
            site="Example Studio",
            release_date=date(2024, 2, 14),
        ),
        [tpdb, stashdb],
    )

    assert resolution.candidate.title == "Known Scene"
    assert stashdb.calls == ["site_date_title"]
    assert tpdb.calls == []


async def test_no_provider_falls_back_to_filename_confidence(session: AsyncSession) -> None:
    resolution = await resolve_metadata_cascade(
        session,
        MetadataSubject(source_path="/data/torrents/scene.mkv", title="Filename Scene"),
        [],
    )

    assert (resolution.candidate.title, resolution.confidence, resolution.tier) == (
        "Filename Scene",
        0.30,
        MetadataTier.FILENAME,
    )
    log = await session.scalar(select(MetadataMatchLog))
    assert log is not None and (log.provider, log.tier, log.outcome) == (
        "filename",
        "filename",
        "matched",
    )


async def test_rate_limited_provider_waits_for_retry_after_then_falls_back(
    session: AsyncSession,
) -> None:
    provider = RateLimitedProvider()
    waits: list[float] = []

    async def sleep(seconds: float) -> None:
        waits.append(seconds)

    resolution = await resolve_metadata_cascade(
        session,
        MetadataSubject(
            source_path="/data/torrents/scene.mkv", title="Filename Scene", oshash="abc"
        ),
        [provider],
        sleep=sleep,
    )

    logs = list(
        await session.scalars(select(MetadataMatchLog).order_by(MetadataMatchLog.created_at))
    )
    assert waits == [12]
    assert provider.attempts == 2
    assert resolution.tier is MetadataTier.FILENAME
    assert [(log.provider, log.outcome, log.detail) for log in logs] == [
        ("limited", "rate_limited", "Retry-After: 12"),
        ("limited", "miss", None),
        ("filename", "matched", None),
    ]


async def test_wrong_match_keeps_the_provider_query_and_result_traceable(
    session: AsyncSession,
) -> None:
    provider = Provider(
        exact=MetadataCandidate(
            title="Wrong Scene",
            site="Other Studio",
            release_date=date(2025, 1, 2),
            provider_id="wrong",
        )
    )
    subject = MetadataSubject(
        source_path="/data/torrents/scene.mkv",
        title="Right Scene",
        site="Studio",
        release_date=date(2026, 1, 2),
    )

    resolution = await resolve_metadata_cascade(session, subject, [provider])
    log = await session.scalar(
        select(MetadataMatchLog).where(MetadataMatchLog.provider == "provider")
    )

    assert resolution.tier is MetadataTier.FILENAME
    assert log is not None
    assert log.query == {"site": "Studio", "release_date": "2026-01-02", "title": "Right Scene"}
    assert log.result == {
        "title": "Wrong Scene",
        "site": "Other Studio",
        "release_date": "2025-01-02",
        "performers": [],
        "studio": None,
        "tags": [],
        "provider_id": "wrong",
    }
