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


async def test_a_site_written_without_its_space_is_the_same_site(session: AsyncSession) -> None:
    """A scene release concatenates what the provider writes with a space.

    `DesiBang.26.08.10.…` is what the indexer answers with; ThePornDB calls the
    same site `Desi Bang`. Compared literally the exact tier never matched a
    real download, and every one of them fell to the filename tier.
    """

    provider = Provider(
        exact=MetadataCandidate(
            title="Amateur Chubby Woman Gets Nailed",
            site="Desi Bang",
            release_date=date(2026, 8, 10),
            provider_id="exact",
        )
    )

    resolution = await resolve_metadata_cascade(
        session,
        MetadataSubject(
            source_path="/data/usenet/completed/scene.mp4",
            title="Amateur Chubby Woman Gets Nailed",
            site="DesiBang",
            release_date=date(2026, 8, 10),
        ),
        [provider],
    )

    assert (resolution.confidence, resolution.tier) == (0.80, MetadataTier.SITE_DATE_TITLE)


async def test_a_different_site_on_the_same_date_is_not_a_match(session: AsyncSession) -> None:
    provider = Provider(
        exact=MetadataCandidate(
            title="Amateur Chubby Woman Gets Nailed",
            site="Some Other Studio",
            release_date=date(2026, 8, 10),
            provider_id="exact",
        )
    )

    resolution = await resolve_metadata_cascade(
        session,
        MetadataSubject(
            source_path="/data/usenet/completed/scene.mp4",
            title="Amateur Chubby Woman Gets Nailed",
            site="DesiBang",
            release_date=date(2026, 8, 10),
        ),
        [provider],
    )

    assert resolution.tier == MetadataTier.FILENAME


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


# --- The cascade as a table (ADR 0005, docs/pipelines/import.md step 5) -------
#
# "fingerprint 0.95, site plus date plus title 0.80, fuzzy title plus performer
# 0.55, filename alone 0.30." One row per tier, each asserting the confidence
# *and* which provider methods were reached, so a tier that answers correctly
# for the wrong reason fails.


class RecordingProvider(Provider):
    """A provider that also remembers what it was asked, not only that it was asked."""

    def __init__(
        self,
        *,
        fingerprint: MetadataCandidate | Exception | None = None,
        exact: MetadataCandidate | None = None,
        fuzzy: list[MetadataCandidate] | None = None,
    ) -> None:
        super().__init__(fingerprint=fingerprint, exact=exact, fuzzy=fuzzy)
        self.fingerprint_queries: list[tuple[str | None, str | None]] = []

    async def find_by_fingerprint(
        self, *, oshash: str | None, perceptual_hash: str | None
    ) -> MetadataCandidate | None:
        self.fingerprint_queries.append((oshash, perceptual_hash))
        return await super().find_by_fingerprint(oshash=oshash, perceptual_hash=perceptual_hash)


FUZZY_SUBJECT = MetadataSubject(
    source_path="/data/torrents/Studio - Scene Title 1080p.mkv",
    title="Scene Title",
    performers=("Performer One",),
)


@pytest.mark.parametrize(
    ("tier", "provider", "subject", "confidence", "reached"),
    [
        (
            "fingerprint",
            Provider(fingerprint=MetadataCandidate(title="Hashed Scene")),
            MetadataSubject(
                source_path="/data/torrents/scene.mkv",
                title="Scene",
                site="Studio",
                release_date=date(2026, 1, 2),
                performers=("Performer One",),
                oshash="abc",
            ),
            0.95,
            ["fingerprint"],
        ),
        (
            "site, date and title",
            Provider(
                exact=MetadataCandidate(
                    title="Scene", site="Studio", release_date=date(2026, 1, 2)
                ),
                fuzzy=[MetadataCandidate(title="Scene", performers=("Performer One",))],
            ),
            MetadataSubject(
                source_path="/data/torrents/scene.mkv",
                title="Scene",
                site="Studio",
                release_date=date(2026, 1, 2),
                performers=("Performer One",),
            ),
            0.80,
            ["site_date_title"],
        ),
        (
            "fuzzy title plus performer",
            Provider(fuzzy=[MetadataCandidate(title="Scene Title", performers=("Performer One",))]),
            FUZZY_SUBJECT,
            0.55,
            ["fuzzy"],
        ),
        (
            "the filename alone",
            Provider(),
            FUZZY_SUBJECT,
            0.30,
            ["fuzzy"],
        ),
    ],
)
async def test_each_cascade_tier_carries_its_documented_confidence(
    session: AsyncSession,
    tier: str,
    provider: Provider,
    subject: MetadataSubject,
    confidence: float,
    reached: list[str],
) -> None:
    resolution = await resolve_metadata_cascade(session, subject, [provider])

    assert resolution.confidence == confidence, tier
    # The tiers below the one that answered are never asked, and the tiers above
    # it are asked exactly once.
    assert provider.calls == reached, tier


async def test_a_fingerprint_match_stops_the_cascade_before_the_cheaper_tiers(
    session: AsyncSession,
) -> None:
    """The `Times.Never` half: a certain answer must not be second-guessed."""
    provider = Provider(
        fingerprint=MetadataCandidate(title="Hashed Scene"),
        exact=MetadataCandidate(title="Other", site="Studio", release_date=date(2026, 1, 2)),
        fuzzy=[MetadataCandidate(title="Other", performers=("Performer One",))],
    )

    resolution = await resolve_metadata_cascade(
        session,
        MetadataSubject(
            source_path="/data/torrents/scene.mkv",
            title="Scene",
            site="Studio",
            release_date=date(2026, 1, 2),
            performers=("Performer One",),
            oshash="abc",
        ),
        [provider],
    )

    assert resolution.candidate.title == "Hashed Scene"
    assert "site_date_title" not in provider.calls
    assert "fuzzy" not in provider.calls


async def test_the_fingerprint_the_import_computed_is_the_key_the_provider_is_given(
    session: AsyncSession,
) -> None:
    """ADR 0034: the oshash doubles as the StashDB lookup key, so it is computed once."""
    provider = RecordingProvider(fingerprint=MetadataCandidate(title="Hashed Scene"))

    await resolve_metadata_cascade(
        session,
        MetadataSubject(source_path="/data/torrents/scene.mkv", title="Scene", oshash="7e086add"),
        [provider],
    )

    assert provider.fingerprint_queries == [("7e086add", None)]


@pytest.mark.parametrize(
    ("what", "candidate_title", "matched"),
    [
        ("an exact title", "The Midnight Session", True),
        ("a title well above the 0.85 gate", "The Moonlight Session", True),
        ("a title below it", "The Midnight Seance", False),
        ("a different scene entirely", "An Afternoon Elsewhere", False),
    ],
)
async def test_the_fuzzy_tier_only_accepts_a_title_above_the_documented_similarity(
    session: AsyncSession, what: str, candidate_title: str, matched: bool
) -> None:
    provider = Provider(
        fuzzy=[MetadataCandidate(title=candidate_title, performers=("Performer One",))]
    )

    resolution = await resolve_metadata_cascade(
        session,
        MetadataSubject(
            source_path="/data/torrents/scene.mkv",
            title="The Midnight Session",
            performers=("Performer One",),
        ),
        [provider],
    )

    assert (resolution.tier is MetadataTier.FUZZY) is matched, what
    assert resolution.confidence == (0.55 if matched else 0.30), what


async def test_the_fuzzy_tier_also_requires_a_performer_in_common(session: AsyncSession) -> None:
    """ "Fuzzy title plus performer": the title alone is not enough."""
    provider = Provider(
        fuzzy=[MetadataCandidate(title="The Midnight Session", performers=("Somebody Else",))]
    )

    resolution = await resolve_metadata_cascade(
        session,
        MetadataSubject(
            source_path="/data/torrents/scene.mkv",
            title="The Midnight Session",
            performers=("Performer One",),
        ),
        [provider],
    )

    assert resolution.tier is MetadataTier.FILENAME


async def test_the_filename_parser_answers_even_with_no_provider_configured(
    session: AsyncSession,
) -> None:
    """ADR 0005: the filename parser is always on, beside the two remote adapters."""
    resolution = await resolve_metadata_cascade(
        session,
        MetadataSubject.from_path(
            Path("/data/torrents/Vixen - Golden Hour (2025-11-02) 1080p WEB-DL x264-GRP.mkv")
        ),
        [],
    )

    assert resolution.tier is MetadataTier.FILENAME
    assert (resolution.candidate.studio, resolution.candidate.title) == ("Vixen", "Golden Hour")
    assert resolution.candidate.release_date == date(2025, 11, 2)
    log = await session.scalar(select(MetadataMatchLog))
    assert log is not None and log.provider == "filename"
