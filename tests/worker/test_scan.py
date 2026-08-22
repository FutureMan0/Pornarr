"""Filesystem scanner behaviour."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from pornarr_db.base import Base
from pornarr_db.models.media import MediaFile
from pornarr_db.models.root_folders import RootFolder
from pornarr_media.probe import ProbeResult
from pornarr_shared.config import Settings
from pornarr_worker.jobs import scan as scan_job
from pornarr_worker.jobs.scan import (
    probe_media_file_job,
    queue_missing_artwork,
    queue_missing_technical_metadata,
    scan_root_folder,
)

ProgressPublisher = Callable[[str, dict[str, object]], Awaitable[None]]


@pytest.fixture
async def session_factory():
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    try:
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()


def _recorded_progress(events: list[tuple[str, dict[str, object]]]) -> ProgressPublisher:
    async def publish(event_type: str, data: dict[str, object]) -> None:
        events.append((event_type, data))

    return publish


async def test_scanner_imports_supported_nonempty_files_and_publishes_progress(
    session_factory, tmp_path: Path
) -> None:
    library = tmp_path / "library"
    library.mkdir()
    first_file = library / "nested" / "First Scene.MP4"
    first_file.parent.mkdir()
    first_file.write_bytes(b"video")
    (library / "empty.mkv").touch()
    (library / "notes.txt").write_text("not media")
    events: list[tuple[str, dict[str, object]]] = []

    async with session_factory() as session:
        folder = RootFolder(path=str(library), free_space_bytes=0)
        session.add(folder)
        await session.flush()

        result = await scan_root_folder(session, folder, _recorded_progress(events))
        await session.commit()

        files = list(await session.scalars(select(MediaFile)))

    assert result == {"imported": 1, "changed": 0, "missing": 0, "scanned": 1}
    assert [(file.path, file.size, file.is_missing) for file in files] == [
        (str(first_file), len(b"video"), False)
    ]
    assert events == [
        (
            "scan.progress",
            {
                "root_folder_id": str(folder.id),
                "files_scanned": 1,
                "current_path": str(first_file),
            },
        )
    ]


async def test_scanner_skips_unchanged_files_without_marking_them_dirty(
    session_factory, tmp_path: Path
) -> None:
    library = tmp_path / "library"
    library.mkdir()
    media_file = library / "scene.mp4"
    media_file.write_bytes(b"video")

    async with session_factory() as session:
        folder = RootFolder(path=str(library), free_space_bytes=0)
        session.add(folder)
        await session.flush()
        await scan_root_folder(session, folder, _recorded_progress([]))
        await session.commit()

        result = await scan_root_folder(session, folder, _recorded_progress([]))

        tracked_file = await session.scalar(select(MediaFile))
        assert tracked_file is not None
        assert tracked_file not in session.sync_session.dirty

    assert result == {"imported": 0, "changed": 0, "missing": 0, "scanned": 1}


async def test_scanner_records_that_it_ran_even_when_nothing_changed(
    session_factory, tmp_path: Path
) -> None:
    """An empty library that never records a scan reads as "never scanned"."""

    library = tmp_path / "library"
    library.mkdir()

    async with session_factory() as session:
        folder = RootFolder(path=str(library), free_space_bytes=0)
        session.add(folder)
        await session.flush()
        assert folder.last_scanned_at is None

        result = await scan_root_folder(session, folder, _recorded_progress([]))
        await session.commit()

    assert result == {"imported": 0, "changed": 0, "missing": 0, "scanned": 0}
    assert folder.last_scanned_at is not None


async def test_scanner_marks_disappeared_files_as_missing(session_factory, tmp_path: Path) -> None:
    library = tmp_path / "library"
    library.mkdir()
    media_file = library / "scene.mp4"
    media_file.write_bytes(b"video")

    async with session_factory() as session:
        folder = RootFolder(path=str(library), free_space_bytes=0)
        session.add(folder)
        await session.flush()
        await scan_root_folder(session, folder, _recorded_progress([]))
        await session.commit()

        media_file.unlink()
        result = await scan_root_folder(session, folder, _recorded_progress([]))
        await session.commit()

        tracked_file = await session.scalar(select(MediaFile))

    assert result == {"imported": 0, "changed": 0, "missing": 1, "scanned": 0}
    assert tracked_file is not None
    assert tracked_file.is_missing is True


async def test_scanner_updates_files_when_size_or_mtime_changes(
    session_factory, tmp_path: Path
) -> None:
    library = tmp_path / "library"
    library.mkdir()
    media_file = library / "scene.mp4"
    media_file.write_bytes(b"video")

    async with session_factory() as session:
        folder = RootFolder(path=str(library), free_space_bytes=0)
        session.add(folder)
        await session.flush()
        await scan_root_folder(session, folder, _recorded_progress([]))
        await session.commit()

        media_file.write_bytes(b"longer video")
        result = await scan_root_folder(session, folder, _recorded_progress([]))
        await session.commit()

        tracked_file = await session.scalar(select(MediaFile))

    assert result == {"imported": 0, "changed": 1, "missing": 0, "scanned": 1}
    assert tracked_file is not None
    assert tracked_file.size == len(b"longer video")
    assert tracked_file.modified_at_ns == media_file.stat().st_mtime_ns


async def test_scanner_cancellation_rolls_back_partial_imports(
    session_factory, tmp_path: Path
) -> None:
    library = tmp_path / "library"
    library.mkdir()
    (library / "first.mp4").write_bytes(b"first")
    (library / "second.mp4").write_bytes(b"second")

    async with session_factory() as session:
        folder = RootFolder(path=str(library), free_space_bytes=0)
        session.add(folder)
        await session.commit()
        folder_id = folder.id

        async def cancel_after_first_progress(_: str, __: dict[str, object]) -> None:
            raise asyncio.CancelledError

        with pytest.raises(asyncio.CancelledError):
            await scan_root_folder(session, folder, cancel_after_first_progress)
        await session.rollback()

        files = list(await session.scalars(select(MediaFile)))
        folder = await session.get(RootFolder, folder_id)
        assert folder is not None
        result = await scan_root_folder(session, folder, _recorded_progress([]))
        await session.commit()

    assert files == []
    assert result == {"imported": 2, "changed": 0, "missing": 0, "scanned": 2}


class RecordingRedis:
    """Enough of ARQ's client to see which artwork was asked for."""

    def __init__(self) -> None:
        self.jobs: list[tuple[str, tuple[object, ...]]] = []

    async def enqueue_job(self, function: str, *args: object, **_: object) -> None:
        self.jobs.append((function, args))


async def test_a_scanned_file_without_a_poster_is_queued_for_artwork(
    session_factory, tmp_path: Path
) -> None:
    library = tmp_path / "library"
    library.mkdir()
    (library / "With Poster.mp4").write_bytes(b"video")
    (library / "Without Poster.mp4").write_bytes(b"video")
    settings = Settings(
        app_secret="0123456789abcdef0123456789abcdef",
        database_url="sqlite+aiosqlite://",
        redis_url="redis://localhost:6379/7",
        data_path=tmp_path,
    )
    redis = RecordingRedis()

    async with session_factory() as session:
        folder = RootFolder(path=str(library), free_space_bytes=0)
        session.add(folder)
        await session.flush()
        await scan_root_folder(session, folder, _recorded_progress([]))
        await session.flush()

        with_poster = await session.scalar(
            select(MediaFile).where(MediaFile.path.endswith("With Poster.mp4"))
        )
        assert with_poster is not None
        poster = settings.thumbnail_path / str(with_poster.media_id) / "poster.jpg"
        poster.parent.mkdir(parents=True)
        poster.write_bytes(b"jpeg")

        queued = await queue_missing_artwork(redis, session, folder, settings)

    assert queued == 1
    assert [args[0] for _, args in redis.jobs] == [str(library / "Without Poster.mp4")]


async def test_a_scanned_file_without_technical_metadata_is_queued_for_a_probe(
    session_factory, tmp_path: Path
) -> None:
    library = tmp_path / "library"
    library.mkdir()
    (library / "Scene.mp4").write_bytes(b"video")
    redis = RecordingRedis()

    async with session_factory() as session:
        folder = RootFolder(path=str(library), free_space_bytes=0)
        session.add(folder)
        await session.flush()
        await scan_root_folder(session, folder, _recorded_progress([]))
        await session.flush()

        media_file = await session.scalar(select(MediaFile))
        assert media_file is not None

        queued = await queue_missing_technical_metadata(redis, session, folder)

    assert queued == 1
    assert redis.jobs == [("probe_media_file_job", (str(media_file.id),))]


async def test_a_scanned_files_probe_records_its_codecs(
    session_factory, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The probe a scan queues writes the same shape the import pipeline does."""

    library = tmp_path / "library"
    library.mkdir()
    (library / "Scene.mp4").write_bytes(b"video")
    technical = ProbeResult(
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

    async with session_factory() as session:
        folder = RootFolder(path=str(library), free_space_bytes=0)
        session.add(folder)
        await session.flush()
        await scan_root_folder(session, folder, _recorded_progress([]))
        await session.commit()
        media_file = await session.scalar(select(MediaFile))
        assert media_file is not None
        assert media_file.codecs is None

        @asynccontextmanager
        async def scope():
            yield session

        monkeypatch.setattr(scan_job, "session_scope", scope)
        monkeypatch.setattr(scan_job, "probe", lambda _path: technical)

        probed = await probe_media_file_job({}, str(media_file.id))

    assert probed is True
    assert media_file.codecs == {
        "container": "mov,mp4,m4a",
        "video": {"codec": "h264", "profile": "High", "level": 40},
        "audio": {"codec": "aac"},
    }
    assert media_file.resolution == "1920x1080"
    assert media_file.duration_seconds == 20.0
    assert media_file.bitrate == 4_000_000
    assert media_file.container == "mov,mp4,m4a"
