"""Import local media files from one configured library root."""

from __future__ import annotations

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
from pornarr_shared.events import publish_event
from pornarr_shared.jobs import job

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


async def scan(context: dict[str, Any], root_folder_id: str, run_id: str) -> dict[str, int]:
    """Run one transactional scan; ARQ retries cancellation or write races safely."""

    del run_id
    async with session_scope() as session:
        folder = await session.get(RootFolder, UUID(root_folder_id))
        if folder is None or not folder.enabled:
            return {"imported": 0, "changed": 0, "missing": 0, "scanned": 0}

        async def progress(event_type: str, data: dict[str, object]) -> None:
            await publish_event(context["redis"], event_type, data)

        return await scan_root_folder(session, folder, progress)


SCAN_JOB = job(scan)
