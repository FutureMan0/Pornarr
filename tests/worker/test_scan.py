"""Filesystem scanner behaviour."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from pornarr_db.base import Base
from pornarr_db.models.media import MediaFile
from pornarr_db.models.root_folders import RootFolder
from pornarr_worker.jobs.scan import scan_root_folder

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
        assert folder not in session.sync_session.dirty

    assert result == {"imported": 0, "changed": 0, "missing": 0, "scanned": 1}


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
