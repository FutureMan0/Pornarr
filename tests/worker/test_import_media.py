"""The step that turns a validated trigger into a library record."""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from uuid import UUID

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from pornarr_db.base import Base
from pornarr_db.models.download import ImportTrigger
from pornarr_db.models.filters import (
    ContentFilterProfile,
    ContentFilterRule,
    FilterAction,
    FilterProfileScope,
    FilterRuleKind,
)
from pornarr_db.models.media import Media, MediaFile
from pornarr_db.models.quarantine import QuarantineItem
from pornarr_db.models.root_folders import RootFolder
from pornarr_db.types import set_cipher
from pornarr_media.probe import MediaProbeError, ProbeResult
from pornarr_shared.config import Settings
from pornarr_shared.crypto import CredentialCipher
from pornarr_worker.jobs.import_media import import_ready_trigger

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
