"""Safely replace one active library file without replacing its media record."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from pornarr_core.library_placement import PlacementMethod, place_file
from pornarr_db.models.media import Media, MediaFile, MediaFileHistory
from pornarr_db.session import session_scope
from pornarr_media.probe import ProbeResult, probe
from pornarr_shared.jobs import job

MINIMUM_MEDIA_FILE_SIZE_BYTES = 1
MAX_DURATION_DIFFERENCE = 0.05
Probe = Callable[[Path], ProbeResult]


class UpgradeVerificationError(Exception):
    """The candidate failed a safety check before it could replace the active file."""


@dataclass(frozen=True)
class UpgradeResult:
    media_file_id: UUID
    path: Path
    placement_method: PlacementMethod


async def upgrade_media_file(
    session: AsyncSession,
    media_id: UUID,
    source: Path,
    library_root: Path,
    *,
    quality: str | None,
    custom_format_score: int = 0,
    probe_file: Probe = probe,
) -> UpgradeResult:
    """Verify a new inactive file before atomically making it the active library file."""
    media = await session.get(Media, media_id, with_for_update=True)
    if media is None:
        raise LookupError("media does not exist")
    old_file = await session.scalar(
        select(MediaFile)
        .where(MediaFile.media_id == media.id, MediaFile.is_active.is_(True))
        .with_for_update()
    )
    if old_file is None:
        raise LookupError("media does not have an active file")

    placed = await asyncio.to_thread(
        place_file,
        source,
        library_root,
        media.studio,
        media.title,
        media.release_date.isoformat() if media.release_date is not None else None,
        quality=quality,
    )
    replacement_stat = await asyncio.to_thread(placed.path.stat)
    replacement = MediaFile(
        media_id=media.id,
        path=str(placed.path),
        size=replacement_stat.st_size,
        modified_at_ns=replacement_stat.st_mtime_ns,
        quality=quality,
        custom_format_score=custom_format_score,
        is_active=False,
    )
    session.add(replacement)
    try:
        await session.flush()
        result = await asyncio.to_thread(probe_file, placed.path)
        _verify_replacement(replacement, old_file, result)
    except Exception:
        await session.delete(replacement)
        await session.flush()
        await asyncio.to_thread(_remove_failed_replacement, placed.path, library_root)
        raise

    replacement.resolution = result.resolution
    codecs: dict[str, object] = {"streams": list(result.codecs)}
    replacement.codecs = codecs
    replacement.duration_seconds = result.duration
    replacement.bitrate = result.bitrate
    replacement.streams = list(result.streams)
    replacement.container = result.container
    old_file.is_active = False
    await session.flush()
    replacement.is_active = True
    session.add(MediaFileHistory(replaced_file_id=old_file.id, replacement_file_id=replacement.id))
    await session.flush()
    try:
        await asyncio.to_thread(Path(old_file.path).unlink)
        old_file.is_missing = True
    except OSError:
        # The activated replacement is safe. Keep the old inactive file record so a later
        # cleanup can retry rather than risking the new active file.
        pass
    return UpgradeResult(replacement.id, placed.path, placed.method)


def _remove_failed_replacement(path: Path, library_root: Path) -> None:
    path.unlink(missing_ok=True)
    root = library_root.resolve()
    parent = path.parent
    while parent != root:
        try:
            parent.rmdir()
        except OSError:
            return
        parent = parent.parent


def _verify_replacement(replacement: MediaFile, old_file: MediaFile, result: ProbeResult) -> None:
    if replacement.size < MINIMUM_MEDIA_FILE_SIZE_BYTES:
        raise UpgradeVerificationError("replacement file is empty")
    if old_file.duration_seconds is None or old_file.duration_seconds <= 0:
        return
    if result.duration is None:
        raise UpgradeVerificationError("replacement duration could not be verified")
    if (
        abs(result.duration - old_file.duration_seconds) / old_file.duration_seconds
        > MAX_DURATION_DIFFERENCE
    ):
        raise UpgradeVerificationError("replacement duration differs too much from the active file")


async def upgrade_media_file_job(
    _: dict[str, Any],
    media_id: str,
    source_path: str,
    library_path: str,
    quality: str | None = None,
    custom_format_score: int = 0,
) -> str:
    """ARQ entrypoint for an already-selected better download."""
    async with session_scope() as session:
        result = await upgrade_media_file(
            session,
            UUID(media_id),
            Path(source_path),
            Path(library_path),
            quality=quality,
            custom_format_score=custom_format_score,
        )
        return str(result.media_file_id)


UPGRADE_MEDIA_FILE_JOB = job(upgrade_media_file_job)
