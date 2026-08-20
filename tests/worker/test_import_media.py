"""The step that turns a validated trigger into a library record."""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from datetime import date
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from pornarr_core.naming import normalize_title, split_release_name
from pornarr_db.base import Base
from pornarr_db.models.audit import AuditLog
from pornarr_db.models.download import DownloadJob, ImportTrigger
from pornarr_db.models.entities import MediaPerformer, MediaTag, Performer, Tag
from pornarr_db.models.filters import (
    ContentFilterProfile,
    ContentFilterRule,
    FilterAction,
    FilterProfileScope,
    FilterRuleKind,
)
from pornarr_db.models.media import Media, MediaFile
from pornarr_db.models.quality import QualityDefinition, QualityProfile, QualityProfileItem
from pornarr_db.models.quarantine import QuarantineItem
from pornarr_db.models.request import Request, RequestStatus
from pornarr_db.models.root_folders import RootFolder
from pornarr_db.models.user import User
from pornarr_db.types import set_cipher
from pornarr_integrations.metadata import MetadataCandidate
from pornarr_media.probe import MediaProbeError, ProbeResult
from pornarr_shared.config import Settings
from pornarr_shared.crypto import CredentialCipher
from pornarr_worker.jobs import import_media as import_media_module
from pornarr_worker.jobs.import_media import IMPORTED, ImportOutcome, import_ready_trigger
from pornarr_worker.jobs.quarantine import QuarantineReasonCode

SECRET = "0123456789abcdef0123456789abcdef"
PROBED = ProbeResult(
    resolution="1920x1080",
    codecs=("h264", "aac"),
    duration=20.0,
    bitrate=4_000_000,
    streams=(
        {"codec_type": "video", "codec_name": "h264", "profile": "High", "level": 40},
        {"codec_type": "audio", "codec_name": "aac"},
    ),
    container="mov,mp4,m4a",
)


@pytest.fixture(autouse=True)
def _cipher() -> Iterator[None]:
    set_cipher(CredentialCipher(SECRET))
    yield
    set_cipher(None)


@pytest.fixture
async def session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as created_session:
        yield created_session
    await engine.dispose()


def settings(data_path: Path) -> Settings:
    return Settings(
        app_secret=SECRET,
        database_url="sqlite+aiosqlite://",
        redis_url="redis://localhost:6379/7",
        data_path=data_path,
    )


async def ready_trigger(session: AsyncSession, data_path: Path, name: str) -> UUID:
    """Stage a real file below the torrent root, as intake leaves it."""

    source = data_path / "torrents" / f"{name}.mp4"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_bytes(b"probe fixture" * 1024)
    trigger = ImportTrigger(source_path=str(source), status="ready")
    session.add(trigger)
    (data_path / "library").mkdir(parents=True, exist_ok=True)
    session.add(RootFolder(path=str(data_path / "library"), free_space_bytes=1))
    await session.flush()
    return trigger.id


async def second_ready_trigger(session: AsyncSession, data_path: Path, name: str) -> UUID:
    """A second file staged against the root folder `ready_trigger` already created.

    `RootFolder.path` is unique, so a test that imports two files needs this
    rather than a second call to `ready_trigger`.
    """

    source = data_path / "torrents" / f"{name}.mp4"
    source.write_bytes(b"probe fixture" * 1024)
    trigger = ImportTrigger(source_path=str(source), status="ready")
    session.add(trigger)
    await session.flush()
    return trigger.id


async def test_places_the_file_and_publishes_the_media(
    session: AsyncSession, tmp_path: Path
) -> None:
    trigger_id = await ready_trigger(session, tmp_path, "Probe Studio - Scene (2026) 1080p")

    outcome = await import_ready_trigger(
        session,
        trigger_id,
        settings(tmp_path),
        probe_file=lambda _: PROBED,
        fingerprint=lambda _: "abc123",
    )

    assert outcome.status == "imported"
    media = await session.get(Media, outcome.media_id)
    assert media is not None
    media_file = await session.scalar(select(MediaFile).where(MediaFile.media_id == media.id))
    assert media_file is not None
    placed = Path(media_file.path)
    assert placed.is_file()
    assert placed.is_relative_to(tmp_path / "library")
    assert media_file.oshash == "abc123"
    assert media_file.resolution == "1920x1080"
    assert media_file.quality == "1080p"
    # The shape `/api/media/{id}/playback-info` reads to decide direct play.
    assert media_file.codecs == {
        "container": "mov,mp4,m4a",
        "video": {"codec": "h264", "profile": "High", "level": 40},
        "audio": {"codec": "aac"},
    }


async def test_refuses_a_file_already_in_the_library(session: AsyncSession, tmp_path: Path) -> None:
    media = Media(title="Existing", normalized_title="existing")
    session.add(
        MediaFile(media=media, path=str(tmp_path / "library" / "x.mp4"), size=1, oshash="dup")
    )
    trigger_id = await ready_trigger(session, tmp_path, "Same Scene 1080p")

    outcome = await import_ready_trigger(
        session,
        trigger_id,
        settings(tmp_path),
        probe_file=lambda _: PROBED,
        fingerprint=lambda _: "dup",
    )

    assert outcome.status == "duplicate"
    assert outcome.media_id is None


async def test_quarantines_a_file_that_cannot_be_probed(
    session: AsyncSession, tmp_path: Path
) -> None:
    trigger_id = await ready_trigger(session, tmp_path, "Unreadable 1080p")

    def unprobeable(_: Path) -> ProbeResult:
        raise MediaProbeError("Media file could not be probed")

    outcome = await import_ready_trigger(
        session,
        trigger_id,
        settings(tmp_path),
        probe_file=unprobeable,
        fingerprint=lambda _: None,
    )

    assert outcome.status == "quarantined"
    item = await session.scalar(select(QuarantineItem))
    assert item is not None
    assert item.reasons[0]["code"] == "unexpected_file_type"
    assert await session.scalar(select(MediaFile)) is None


async def test_a_reject_rule_stops_the_import_before_placement(
    session: AsyncSession, tmp_path: Path
) -> None:
    profile = ContentFilterProfile(scope=FilterProfileScope.GLOBAL)
    session.add(profile)
    session.add(
        ContentFilterRule(
            profile=profile,
            kind=FilterRuleKind.TERM,
            pattern="forbidden",
            action=FilterAction.REJECT,
            enabled=True,
        )
    )
    trigger_id = await ready_trigger(session, tmp_path, "Forbidden Scene 1080p")

    outcome = await import_ready_trigger(
        session,
        trigger_id,
        settings(tmp_path),
        probe_file=lambda _: PROBED,
        fingerprint=lambda _: None,
    )

    assert outcome.status == "failed"
    trigger = await session.get(ImportTrigger, trigger_id)
    assert trigger is not None
    assert trigger.error_code == "filter_rule"
    assert await session.scalar(select(MediaFile)) is None


@pytest.mark.parametrize(
    ("action", "status", "placed", "quarantined"),
    [
        (FilterAction.ALLOW, "imported", True, False),
        (FilterAction.QUARANTINE, "quarantined", False, True),
        (FilterAction.REJECT, "failed", False, False),
    ],
)
async def test_the_filter_outcome_is_exactly_one_of_allow_quarantine_reject(
    session: AsyncSession,
    tmp_path: Path,
    action: FilterAction,
    status: str,
    placed: bool,
    quarantined: bool,
) -> None:
    """Step 8: "The outcome is allow, quarantine or reject." All three, end to end."""
    profile = ContentFilterProfile(scope=FilterProfileScope.GLOBAL)
    session.add(profile)
    session.add(
        ContentFilterRule(
            profile=profile,
            kind=FilterRuleKind.TERM,
            pattern="marked",
            action=action,
            enabled=True,
        )
    )
    trigger_id = await ready_trigger(session, tmp_path, "Marked Scene 1080p")

    outcome = await import_ready_trigger(
        session,
        trigger_id,
        settings(tmp_path),
        probe_file=lambda _: PROBED,
        fingerprint=lambda _: None,
    )

    assert outcome.status == status
    assert (await session.scalar(select(MediaFile)) is not None) is placed
    assert (await session.scalar(select(QuarantineItem)) is not None) is quarantined


async def test_a_quarantined_import_names_the_rule_that_diverted_it(
    session: AsyncSession, tmp_path: Path
) -> None:
    """The reason, not just the verdict: which rule, and what it matched on."""
    profile = ContentFilterProfile(scope=FilterProfileScope.GLOBAL)
    session.add(profile)
    rule = ContentFilterRule(
        profile=profile,
        kind=FilterRuleKind.TERM,
        pattern="marked",
        action=FilterAction.QUARANTINE,
        enabled=True,
    )
    session.add(rule)
    await session.flush()
    trigger_id = await ready_trigger(session, tmp_path, "Marked Scene 1080p")

    await import_ready_trigger(
        session,
        trigger_id,
        settings(tmp_path),
        probe_file=lambda _: PROBED,
        fingerprint=lambda _: None,
    )

    item = await session.scalar(select(QuarantineItem))
    assert item is not None
    assert item.reasons[0]["code"] == "filter_rule"
    assert item.reasons[0]["evidence"] == {"rule_id": str(rule.id), "pattern": "marked"}
    # It never reaches placement, and it is not in the library.
    assert await session.scalar(select(MediaFile)) is None
    assert Path(item.quarantine_path).is_file()


async def test_a_disabled_rule_never_fires(session: AsyncSession, tmp_path: Path) -> None:
    """The six rules the setup wizard creates are disabled; they must do nothing."""
    profile = ContentFilterProfile(scope=FilterProfileScope.GLOBAL)
    session.add(profile)
    session.add(
        ContentFilterRule(
            profile=profile,
            kind=FilterRuleKind.TERM,
            pattern="marked",
            action=FilterAction.REJECT,
            enabled=False,
        )
    )
    trigger_id = await ready_trigger(session, tmp_path, "Marked Scene 1080p")

    outcome = await import_ready_trigger(
        session,
        trigger_id,
        settings(tmp_path),
        probe_file=lambda _: PROBED,
        fingerprint=lambda _: None,
    )

    assert outcome.status == "imported"


async def test_the_recorded_reason_codes_are_not_the_ones_the_api_contract_names(
    session: AsyncSession, tmp_path: Path
) -> None:
    """`docs/api-contract.md:33` names `DUPLICATE_IN_LIBRARY`; the trigger says `duplicate`.

    Recorded rather than asserted away: the contract says the codes are stable
    and machine-readable, and the import pipeline uses a different vocabulary
    from the one the contract publishes. The same holds for
    `METADATA_CONFIDENCE_LOW`, which the quarantine reason spells
    `low_confidence`.
    """
    media = Media(title="Existing", normalized_title="existing")
    session.add(
        MediaFile(media=media, path=str(tmp_path / "library" / "x.mp4"), size=1, oshash="dup")
    )
    trigger_id = await ready_trigger(session, tmp_path, "Same Scene 1080p")

    await import_ready_trigger(
        session,
        trigger_id,
        settings(tmp_path),
        probe_file=lambda _: PROBED,
        fingerprint=lambda _: "dup",
    )

    trigger = await session.get(ImportTrigger, trigger_id)
    assert trigger is not None
    assert trigger.error_code == "duplicate"
    assert trigger.error_code != "DUPLICATE_IN_LIBRARY"
    assert QuarantineReasonCode.LOW_CONFIDENCE.value == "low_confidence"
    assert QuarantineReasonCode.LOW_CONFIDENCE.value != "METADATA_CONFIDENCE_LOW"


@pytest.mark.parametrize(
    ("verdict", "allowed", "cutoff", "existing_quality", "release"),
    [
        # "Not in the profile": the profile allows 480p and the release is 1080p.
        ("not in the profile", "480p", "480p", None, "Out Of Profile 1080p"),
        # "Not an upgrade": the same media already holds a file of the same
        # quality, so the new one is worth nothing more than what is there. The
        # release states a studio and a date because that is what makes it the
        # same media: step 3's fuzzy half is what tells step 7 which file this
        # release would replace.
        (
            "not an upgrade",
            "1080p",
            "2160p",
            "1080p",
            "Held Studio - Same Again (2026-01-01) 1080p",
        ),
        # "Already past cutoff": the release is genuinely better than the held
        # file, and is still refused because the held file already reaches the
        # cutoff. It has to be better, or `decide_quality` answers "not an
        # upgrade" first and this row would prove nothing the one above does not.
        (
            "already past cutoff",
            "2160p",
            "1080p",
            "1080p",
            "Held Studio - Past Cutoff (2026-01-01) 2160p",
        ),
    ],
)
async def test_the_quality_verdict_ends_the_import_with_a_reason(
    session: AsyncSession,
    tmp_path: Path,
    verdict: str,
    allowed: str,
    cutoff: str,
    existing_quality: str | None,
    release: str,
) -> None:
    """All three verdicts `docs/pipelines/import.md:26-27` names, not just the first."""
    definitions = {
        name: QualityDefinition(
            name=name,
            resolution=name,
            source="web",
            weight=weight,
            minimum_size_mb_per_minute=0,
            maximum_size_mb_per_minute=1_000,
        )
        for weight, name in enumerate(("480p", "1080p", "2160p"), start=1)
    }
    session.add_all(definitions.values())
    await session.flush()
    profile = QualityProfile(
        name=f"{allowed} only", cutoff_quality_id=definitions[cutoff].id, is_default=True
    )
    profile.items = [QualityProfileItem(quality_definition_id=definitions[allowed].id, position=0)]
    session.add(profile)
    if existing_quality is not None:
        # The held file has to be the file this release would replace, or "not
        # an upgrade" and "past cutoff" have nothing to be about. Step 3 calls a
        # release an upgrade candidate only when the studio, the title, the
        # release date and the duration all agree, so the held media is built
        # from the release's own name and the probe's own duration.
        held = split_release_name(release)
        media = Media(
            title=held.title,
            normalized_title=normalize_title(held.title),
            studio=held.studio,
            release_date=date(2026, 1, 1),
        )
        session.add(
            MediaFile(
                media=media,
                path=str(tmp_path / "library" / "held.mp4"),
                size=1,
                quality=existing_quality,
                duration_seconds=PROBED.duration,
            )
        )
    trigger_id = await ready_trigger(session, tmp_path, release)

    outcome = await import_ready_trigger(
        session,
        trigger_id,
        settings(tmp_path),
        probe_file=lambda _: PROBED,
        fingerprint=lambda _: None,
    )

    trigger = await session.get(ImportTrigger, trigger_id)
    assert trigger is not None
    assert outcome.status != "imported", verdict
    assert trigger.error_code is not None
    assert "quality" in trigger.error_code.casefold()


async def test_a_user_rule_tightens_the_global_outcome_at_import_time(
    session: AsyncSession, tmp_path: Path
) -> None:
    """The user profile tightens: global allows, the user quarantines, quarantine wins.

    Whose profile that is, is the whole question. The download is here because
    this reader requested it, and the trigger reaches back to them through the
    download job their `Request` was attached to - which is the only link an
    import has to a person, and the reason no other account's rules are read.
    """
    owner = User(username="viewer", password_hash="x")
    session.add(owner)
    await session.flush()
    global_profile = ContentFilterProfile(scope=FilterProfileScope.GLOBAL)
    user_profile = ContentFilterProfile(scope=FilterProfileScope.USER, user_id=owner.id)
    session.add_all([global_profile, user_profile])
    session.add_all(
        [
            ContentFilterRule(
                profile=global_profile,
                kind=FilterRuleKind.TERM,
                pattern="marked",
                action=FilterAction.ALLOW,
                enabled=True,
            ),
            ContentFilterRule(
                profile=user_profile,
                kind=FilterRuleKind.TERM,
                pattern="marked",
                action=FilterAction.QUARANTINE,
                enabled=True,
            ),
        ]
    )
    trigger_id = await ready_trigger(session, tmp_path, "Marked Scene 1080p")
    await _requested_by(session, trigger_id, owner)

    outcome = await import_ready_trigger(
        session,
        trigger_id,
        settings(tmp_path),
        probe_file=lambda _: PROBED,
        fingerprint=lambda _: None,
    )

    assert outcome.status == "quarantined"
    assert await session.scalar(select(MediaFile)) is None


async def test_another_readers_rule_does_not_tighten_an_import_they_did_not_ask_for(
    session: AsyncSession, tmp_path: Path
) -> None:
    """The other side of the same rule, which is what keeps it from being a bug.

    A user profile tightens the import its owner asked for. Reading every user
    profile instead would let one reader's rule about their own view decide what
    the instance keeps on disk for everybody, which is not what "the user
    profile only ever tightening the global one" grants.
    """
    stranger = User(username="stranger", password_hash="x")
    session.add(stranger)
    await session.flush()
    user_profile = ContentFilterProfile(scope=FilterProfileScope.USER, user_id=stranger.id)
    session.add(user_profile)
    session.add(
        ContentFilterRule(
            profile=user_profile,
            kind=FilterRuleKind.TERM,
            pattern="marked",
            action=FilterAction.QUARANTINE,
            enabled=True,
        )
    )
    trigger_id = await ready_trigger(session, tmp_path, "Marked Scene 1080p")

    outcome = await import_ready_trigger(
        session,
        trigger_id,
        settings(tmp_path),
        probe_file=lambda _: PROBED,
        fingerprint=lambda _: None,
    )

    assert outcome.status == "imported"


async def _requested_by(session: AsyncSession, trigger_id: UUID, user: User) -> None:
    """Attach the trigger to a download job this user's request produced."""

    job = DownloadJob(client_name="qbittorrent", protocol="torrent", release_guid="guid")
    session.add(job)
    await session.flush()
    session.add(
        Request(
            user_id=user.id,
            query="marked",
            status=RequestStatus.DOWNLOADING,
            download_job_id=job.id,
        )
    )
    trigger = await session.get(ImportTrigger, trigger_id)
    assert trigger is not None
    trigger.download_job_id = job.id
    await session.flush()


async def test_every_firing_filter_rule_is_written_to_the_audit_log(
    session: AsyncSession, tmp_path: Path
) -> None:
    profile = ContentFilterProfile(scope=FilterProfileScope.GLOBAL)
    session.add(profile)
    rule = ContentFilterRule(
        profile=profile,
        kind=FilterRuleKind.TERM,
        pattern="marked",
        action=FilterAction.QUARANTINE,
        enabled=True,
    )
    session.add(rule)
    await session.flush()
    trigger_id = await ready_trigger(session, tmp_path, "Marked Scene 1080p")

    await import_ready_trigger(
        session,
        trigger_id,
        settings(tmp_path),
        probe_file=lambda _: PROBED,
        fingerprint=lambda _: None,
    )

    records = list(await session.scalars(select(AuditLog)))
    assert [record.target for record in records] == [str(rule.id)]


async def test_the_database_itself_refuses_a_second_active_file_for_one_media(
    session: AsyncSession, tmp_path: Path
) -> None:
    """ADR 0032: the one-active-row rule is enforced in the database, not only in code."""
    media = Media(title="One", normalized_title="one")
    session.add_all(
        [
            MediaFile(media=media, path=str(tmp_path / "a.mp4"), size=1, is_active=True),
            MediaFile(media=media, path=str(tmp_path / "b.mp4"), size=1, is_active=True),
        ]
    )

    with pytest.raises(IntegrityError):
        await session.flush()
    await session.rollback()


async def test_a_replaced_inactive_file_may_sit_beside_the_active_one(
    session: AsyncSession, tmp_path: Path
) -> None:
    """The other side of the constraint: history is only possible if inactive rows may pile up."""
    media = Media(title="One", normalized_title="one")
    session.add_all(
        [
            MediaFile(media=media, path=str(tmp_path / "a.mp4"), size=1, is_active=True),
            MediaFile(media=media, path=str(tmp_path / "b.mp4"), size=1, is_active=False),
            MediaFile(media=media, path=str(tmp_path / "c.mp4"), size=1, is_active=False),
        ]
    )

    await session.flush()

    assert len(list(await session.scalars(select(MediaFile)))) == 3


async def test_import_enqueues_the_sprite_job_alongside_the_artwork_job(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Step 10 names four artefacts; import_media.py:188 used to enqueue only the poster."""
    media_id = uuid4()
    enqueued: list[tuple[str, tuple[object, ...]]] = []

    async def fake_ready_trigger(*_: object, **__: object) -> ImportOutcome:
        return ImportOutcome(IMPORTED, media_id, "/data/library/movie.mp4")

    @asynccontextmanager
    async def fake_session_scope() -> AsyncIterator[None]:
        yield None

    async def fake_configured_providers(_: object) -> list[object]:
        return []

    async def fake_enqueue_once(_redis: object, function: str, *args: object, **__: object) -> None:
        enqueued.append((function, args))

    async def fake_publish_event(*_: object, **__: object) -> None:
        return None

    monkeypatch.setattr(import_media_module, "import_ready_trigger", fake_ready_trigger)
    monkeypatch.setattr(import_media_module, "session_scope", fake_session_scope)
    monkeypatch.setattr(import_media_module, "get_settings", lambda: settings(tmp_path))
    monkeypatch.setattr(import_media_module, "configured_providers", fake_configured_providers)
    monkeypatch.setattr(import_media_module, "enqueue_once", fake_enqueue_once)
    monkeypatch.setattr(import_media_module, "publish_event", fake_publish_event)

    result = await import_media_module.import_media({"redis": object()}, str(uuid4()))

    assert result == IMPORTED
    assert [name for name, _ in enqueued] == [
        import_media_module.ARTWORK_JOB_NAME,
        import_media_module.SPRITE_JOB_NAME,
    ]
    _, sprite_args = enqueued[1]
    assert sprite_args == (
        "/data/library/movie.mp4",
        str(settings(tmp_path).thumbnail_path / str(media_id)),
    )


async def test_a_fuzzy_match_upgrades_the_existing_media_instead_of_a_second_one(
    session: AsyncSession, tmp_path: Path
) -> None:
    """Step 3's fuzzy half: title, studio, date and duration all inside their thresholds."""
    first_id = await ready_trigger(
        session, tmp_path, "Probe Studio - Sunny Afternoon (2026-03-04) 720p"
    )
    first = await import_ready_trigger(
        session,
        first_id,
        settings(tmp_path),
        probe_file=lambda _: PROBED,
        fingerprint=lambda _: "sunny-one",
    )
    assert first.status == "imported"

    second_id = await second_ready_trigger(
        session, tmp_path, "Probe Studio - Sunny Afternoons (2026-03-05) 2160p"
    )
    second = await import_ready_trigger(
        session,
        second_id,
        settings(tmp_path),
        probe_file=lambda _: PROBED,
        fingerprint=lambda _: "sunny-two",
    )

    assert second.status == "imported"
    assert second.media_id == first.media_id
    media = await session.get(Media, first.media_id)
    assert media is not None
    # The newer scrape's metadata, not the one the first import recorded.
    assert media.title == "Sunny Afternoons"
    files = list(
        await session.scalars(select(MediaFile).where(MediaFile.media_id == first.media_id))
    )
    assert sum(file.is_active for file in files) == 1


async def test_a_release_date_outside_the_window_is_not_a_fuzzy_match(
    session: AsyncSession, tmp_path: Path
) -> None:
    """Title, studio and duration alone are not enough; the date has to be close too."""
    first_id = await ready_trigger(
        session, tmp_path, "Probe Studio - Twilight Echo (2026-03-04) 720p"
    )
    first = await import_ready_trigger(
        session,
        first_id,
        settings(tmp_path),
        probe_file=lambda _: PROBED,
        fingerprint=lambda _: "twilight-one",
    )
    assert first.status == "imported"

    # Sixteen days out, well past the two-day window `detect_duplicate` allows.
    second_id = await second_ready_trigger(
        session, tmp_path, "Probe Studio - Twilight Echoes (2026-03-20) 2160p"
    )
    second = await import_ready_trigger(
        session,
        second_id,
        settings(tmp_path),
        probe_file=lambda _: PROBED,
        fingerprint=lambda _: "twilight-two",
    )

    assert second.status == "imported"
    assert second.media_id != first.media_id


class TaggingProvider:
    """A provider that knows the scene, the way a live one does."""

    name = "tpdb"
    precedence = 1

    async def find_by_fingerprint(
        self, *, oshash: str | None, perceptual_hash: str | None
    ) -> MetadataCandidate | None:
        del oshash, perceptual_hash
        return None

    async def find_by_site_date_title(
        self, *, site: str, release_date: object, title: str
    ) -> MetadataCandidate | None:
        del site, release_date
        return MetadataCandidate(
            title=title,
            studio="Desi Bang",
            site="Desi Bang",
            release_date=date(2026, 8, 10),
            tags=("Amateur", "BBW", "amateur"),
            performers=("Ada Example", "Bo Sample"),
        )

    async def search(self, *, title: str, performers: tuple[str, ...]) -> list[MetadataCandidate]:
        del title, performers
        return []


async def test_the_scene_metadata_a_provider_returned_is_written_to_the_library(
    session: AsyncSession, tmp_path: Path
) -> None:
    """ADR 0005 L9 tier two, all the way onto the record.

    The cascade's tags and performers only ever reached `evaluate_filters`.
    Nothing wrote them, so `/api/media/{id}` answered with empty lists however
    much a provider knew, tag filtering matched nothing, and the interest
    profile that recommendations are built from had no signal to read.
    """

    trigger_id = await ready_trigger(
        session, tmp_path, "DesiBang.26.08.10.Amateur.Chubby.Woman.Gets.Nailed.XXX.1080p"
    )

    outcome = await import_ready_trigger(
        session,
        trigger_id,
        settings(tmp_path),
        adapters=[TaggingProvider()],
        probe_file=lambda _: PROBED,
        fingerprint=lambda _: "sitedatetitle",
    )

    assert outcome.status == "imported"
    media = await session.get(Media, outcome.media_id)
    assert media is not None
    assert (media.studio, media.confidence) == ("Desi Bang", 0.80)

    tags = (
        await session.execute(
            select(Tag.name, MediaTag.confidence, MediaTag.source)
            .join(MediaTag, MediaTag.tag_id == Tag.id)
            .where(MediaTag.media_id == media.id)
        )
    ).all()
    # "Amateur" and "amateur" are one tag, and the provider that supplied them
    # is the source, so a correction can be told apart from a scrape.
    assert sorted(name for name, _, _ in tags) == ["Amateur", "BBW"]
    assert {source for _, _, source in tags} == {"tpdb"}
    assert {confidence for _, confidence, _ in tags} == {0.80}

    performers = (
        await session.execute(
            select(Performer.name)
            .join(MediaPerformer, MediaPerformer.performer_id == Performer.id)
            .where(MediaPerformer.media_id == media.id)
        )
    ).scalars()
    assert sorted(performers) == ["Ada Example", "Bo Sample"]


async def test_a_second_import_reuses_the_tag_and_performer_rows(
    session: AsyncSession, tmp_path: Path
) -> None:
    """One vocabulary, however many titles use it."""

    names = ("DesiBang.26.08.10.One.Scene.XXX.1080p", "DesiBang.26.08.10.Two.Scene.XXX.1080p")
    for index, name in enumerate(names):
        trigger_id = await (
            ready_trigger(session, tmp_path, name)
            if index == 0
            else second_ready_trigger(session, tmp_path, name)
        )
        await import_ready_trigger(
            session,
            trigger_id,
            settings(tmp_path),
            adapters=[TaggingProvider()],
            probe_file=lambda _: PROBED,
            fingerprint=lambda _, name=name: name,
        )

    assert len((await session.execute(select(Tag))).scalars().all()) == 2
    assert len((await session.execute(select(Performer))).scalars().all()) == 2
