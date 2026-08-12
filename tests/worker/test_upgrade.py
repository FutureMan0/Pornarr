"""Upgrade replacement preserves the logical media record and its user state."""

from __future__ import annotations

from collections.abc import Callable
from datetime import date
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from pornarr_db.base import Base
from pornarr_db.models.entities import MediaTag, Tag
from pornarr_db.models.media import Media, MediaFile, MediaFileHistory
from pornarr_db.models.playback import PlaybackProgress, UserEvent, UserEventType
from pornarr_db.models.user import User
from pornarr_media.probe import ProbeResult
from pornarr_worker.jobs.upgrade import UpgradeVerificationError, upgrade_media_file


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


async def test_upgrade_keeps_media_assignments_and_history_while_replacing_the_file(
    session: AsyncSession, tmp_path: Path
) -> None:
    media, old_file = await _media_with_existing_file(session, tmp_path)
    source = tmp_path / "downloads" / "replacement.mkv"
    source.parent.mkdir()
    source.write_bytes(b"replacement")

    result = await upgrade_media_file(
        session,
        media.id,
        source,
        tmp_path / "library",
        quality="2160p",
        custom_format_score=10,
        probe_file=_probe(103),
    )
    await session.flush()

    replacement = await session.get(MediaFile, result.media_file_id)
    history = await session.scalar(select(MediaFileHistory))
    tags = list(await session.scalars(select(MediaTag).where(MediaTag.media_id == media.id)))
    progress = await session.scalar(
        select(PlaybackProgress).where(PlaybackProgress.media_id == media.id)
    )
    events = list(await session.scalars(select(UserEvent).where(UserEvent.media_id == media.id)))

    assert replacement is not None
    assert replacement.is_active is True
    assert replacement.quality == "2160p"
    assert replacement.custom_format_score == 10
    assert old_file.is_active is False
    assert old_file.is_missing is True
    assert history is not None
    assert (history.replaced_file_id, history.replacement_file_id) == (old_file.id, replacement.id)
    assert len(tags) == 1
    assert progress is not None and progress.position_seconds == 20
    assert len(events) == 1
    assert not Path(old_file.path).exists()
    assert [path for path in (tmp_path / "library").rglob("*") if path.is_file()] == [result.path]
    assert source.stat().st_ino == result.path.stat().st_ino


async def test_failed_upgrade_verification_keeps_the_original_file_active_and_intact(
    session: AsyncSession, tmp_path: Path
) -> None:
    media, old_file = await _media_with_existing_file(session, tmp_path)
    source = tmp_path / "downloads" / "too-long.mkv"
    source.parent.mkdir()
    source.write_bytes(b"replacement")

    with pytest.raises(UpgradeVerificationError, match="duration differs"):
        await upgrade_media_file(
            session,
            media.id,
            source,
            tmp_path / "library",
            quality="2160p",
            probe_file=_probe(120),
        )
    await session.flush()

    files = list(await session.scalars(select(MediaFile).where(MediaFile.media_id == media.id)))
    assert files == [old_file]
    assert old_file.is_active is True
    assert old_file.is_missing is False
    assert Path(old_file.path).read_bytes() == b"old"
    assert source.read_bytes() == b"replacement"
    assert not (tmp_path / "library" / "Example Studio").exists()
    assert list(await session.scalars(select(MediaFileHistory))) == []


async def _media_with_existing_file(
    session: AsyncSession, tmp_path: Path
) -> tuple[Media, MediaFile]:
    old_path = tmp_path / "library" / "old.mkv"
    old_path.parent.mkdir()
    old_path.write_bytes(b"old")
    media = Media(
        title="Example Scene",
        normalized_title="example scene",
        studio="Example Studio",
        release_date=date(2024, 1, 1),
    )
    old_file = MediaFile(
        media=media,
        path=str(old_path),
        size=old_path.stat().st_size,
        duration_seconds=100,
        quality="1080p",
    )
    user = User(username="viewer", password_hash="hash")
    tag = Tag(name="Example", normalized_name="example")
    session.add_all((media, old_file, user, tag))
    await session.flush()
    session.add_all(
        (
            MediaTag(media_id=media.id, tag_id=tag.id, confidence=1, source="metadata"),
            PlaybackProgress(
                user_id=user.id,
                media_id=media.id,
                position_seconds=20,
                duration_seconds=100,
            ),
            UserEvent(user_id=user.id, media_id=media.id, event_type=UserEventType.FAVOURITE.value),
        )
    )
    await session.flush()
    return media, old_file


def _probe(duration: float) -> Callable[[Path], ProbeResult]:
    return lambda _: ProbeResult(
        resolution="3840x2160",
        codecs=("hevc", "aac"),
        duration=duration,
        bitrate=1_000_000,
        streams=({"codec_name": "hevc"},),
        container="matroska",
    )
