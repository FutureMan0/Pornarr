"""Import local media files from one configured library root."""

from __future__ import annotations

import asyncio
import os
import stat
from collections.abc import Awaitable, Callable, Iterator
from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from pornarr_db.base import utcnow
from pornarr_db.models.media import Media, MediaFile
from pornarr_db.models.root_folders import RootFolder
from pornarr_db.session import session_scope
from pornarr_media.probe import MediaProbeError, codec_payload, probe
from pornarr_shared.config import Settings, get_settings
from pornarr_shared.events import publish_event
from pornarr_shared.jobs import TRANSCODE_QUEUE, enqueue_once, job

ARTWORK_JOB_NAME = "generate_artwork_job"
PROBE_JOB_NAME = "probe_media_file_job"
MEDIA_EXTENSIONS = frozenset({".avi", ".m4v", ".mkv", ".mov", ".mp4", ".webm", ".wmv"})
MINIMUM_MEDIA_FILE_SIZE_BYTES = 1

ProgressPublisher = Callable[[str, dict[str, object]], Awaitable[None]]


def _iter_media_files(root: Path) -> Iterator[tuple[Path, os.stat_result]]:
    """Yield regular supported files in deterministic path order."""

    for directory, subdirectories, names in os.walk(root):
        subdirectories.sort()
        for name in sorted(names):
            path = Path(directory, name)
            if path.suffix.lower() not in MEDIA_EXTENSIONS:
                continue
            try:
                file_stat = path.stat()
            except OSError:
                continue
            if (
                not stat.S_ISREG(file_stat.st_mode)
                or file_stat.st_size < MINIMUM_MEDIA_FILE_SIZE_BYTES
            ):
                continue
            yield path, file_stat


async def scan_root_folder(
    session: AsyncSession, folder: RootFolder, publish_progress: ProgressPublisher
) -> dict[str, int]:
    """Synchronize one root folder without hashing unchanged files."""

    root_path = Path(folder.path)
    path_prefix = f"{root_path}{os.sep}"
    existing_files = {
        media_file.path: media_file
        for media_file in await session.scalars(
            select(MediaFile).where(MediaFile.path.startswith(path_prefix))
        )
    }
    seen_paths: set[str] = set()
    result = {"imported": 0, "changed": 0, "missing": 0, "scanned": 0}

    for path, file_stat in _iter_media_files(root_path):
        path_string = str(path)
        seen_paths.add(path_string)
        result["scanned"] += 1
        media_file = existing_files.get(path_string)
        if media_file is None:
            media = Media(title=path.stem, normalized_title=path.stem.casefold())
            session.add(
                MediaFile(
                    media=media,
                    path=path_string,
                    size=file_stat.st_size,
                    modified_at_ns=file_stat.st_mtime_ns,
                )
            )
            result["imported"] += 1
        elif (
            media_file.size != file_stat.st_size
            or media_file.modified_at_ns != file_stat.st_mtime_ns
            or media_file.is_missing
        ):
            media_file.size = file_stat.st_size
            media_file.modified_at_ns = file_stat.st_mtime_ns
            media_file.is_missing = False
            result["changed"] += 1

        await publish_progress(
            "scan.progress",
            {
                "root_folder_id": str(folder.id),
                "files_scanned": result["scanned"],
                "current_path": path_string,
            },
        )

    for media_file in existing_files.values():
        if media_file.path not in seen_paths and not media_file.is_missing:
            media_file.is_missing = True
            result["missing"] += 1

    # Stamped even when nothing changed: a clean scan of an unchanged library is
    # still a scan, and leaving the column null would tell the operator it never
    # ran.
    folder.last_scanned_at = utcnow()
    return result


async def queue_missing_artwork(
    redis: Any, session: AsyncSession, folder: RootFolder, settings: Settings
) -> int:
    """Ask for artwork for every file in this root that still has none.

    A scanned file arrives without a poster, and the library screen is a grid of
    posters, so every card was a broken image and a 404 in the console. Asking
    by absence rather than only for new files also repairs a library scanned
    before artwork existed.
    """

    path_prefix = f"{Path(folder.path)}{os.sep}"
    queued = 0
    for media_file in await session.scalars(
        select(MediaFile).where(
            MediaFile.path.startswith(path_prefix),
            MediaFile.is_active.is_(True),
            MediaFile.is_missing.is_(False),
        )
    ):
        poster = settings.thumbnail_path / str(media_file.media_id) / "poster.jpg"
        if poster.is_file():
            continue
        await enqueue_once(
            redis,
            ARTWORK_JOB_NAME,
            media_file.path,
            str(settings.thumbnail_path),
            str(media_file.media_id),
            queue=TRANSCODE_QUEUE,
        )
        queued += 1
    return queued


async def queue_missing_technical_metadata(
    redis: Any, session: AsyncSession, folder: RootFolder
) -> int:
    """Ask for a probe for every file in this root that still has none.

    A file adopted straight from disk gets a path, a size and an mtime and
    nothing ffprobe would have told it, so `decide_direct_play` sees four
    unknowns and can never offer direct play regardless of what the player
    supports. Asking by absence rather than only for new files also repairs a
    library scanned before this existed. Probing each file inline here would
    block a scan of a large library behind however slow ffprobe is on every
    one of them, so the work is queued the same way missing artwork is.
    """

    path_prefix = f"{Path(folder.path)}{os.sep}"
    queued = 0
    for media_file_id in await session.scalars(
        select(MediaFile.id).where(
            MediaFile.path.startswith(path_prefix),
            MediaFile.is_active.is_(True),
            MediaFile.is_missing.is_(False),
            MediaFile.codecs.is_(None),
        )
    ):
        await enqueue_once(redis, PROBE_JOB_NAME, str(media_file_id), queue=TRANSCODE_QUEUE)
        queued += 1
    return queued


async def probe_media_file_job(context: dict[str, Any], media_file_id: str) -> bool:
    """Fill in the technical metadata a scan itself never collects.

    Reuses the same `probe()` and `codec_payload()` the import pipeline calls
    so a scanned file and an imported file end up recorded in the same shape.
    """

    async with session_scope() as session:
        media_file = await session.get(MediaFile, UUID(media_file_id))
        if media_file is None or media_file.codecs is not None:
            return False
        try:
            technical = await asyncio.to_thread(probe, Path(media_file.path))
        except MediaProbeError:
            return False
        media_file.codecs = codec_payload(technical)
        media_file.resolution = technical.resolution
        media_file.duration_seconds = technical.duration
        media_file.bitrate = technical.bitrate
        media_file.streams = [dict(stream) for stream in technical.streams]
        media_file.container = technical.container
        return True


PROBE_MEDIA_FILE_JOB = job(probe_media_file_job)


async def scan(context: dict[str, Any], root_folder_id: str, run_id: str) -> dict[str, int]:
    """Run one transactional scan; ARQ retries cancellation or write races safely."""

    del run_id
    async with session_scope() as session:
        folder = await session.get(RootFolder, UUID(root_folder_id))
        if folder is None or not folder.enabled:
            return {"imported": 0, "changed": 0, "missing": 0, "scanned": 0}

        async def progress(event_type: str, data: dict[str, object]) -> None:
            await publish_event(context["redis"], event_type, data)

        result = await scan_root_folder(session, folder, progress)
        await session.flush()
        await queue_missing_artwork(context["redis"], session, folder, get_settings())
        await queue_missing_technical_metadata(context["redis"], session, folder)
        # Without this the only way to know a scan ended is to notice the
        # progress events stopping, which is indistinguishable from a worker
        # that died mid-walk.
        await progress(
            "scan.completed",
            {
                "root_folder_id": str(folder.id),
                **{key: int(value) for key, value in result.items()},
            },
        )
        return result


SCAN_JOB = job(scan)
