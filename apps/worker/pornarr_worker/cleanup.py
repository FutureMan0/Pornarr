"""Nightly cleanup for regenerable HLS segment directories."""

from __future__ import annotations

import asyncio
import logging
import shutil
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any
from uuid import UUID

from pornarr_media.sessions import TranscodeSessionRegistry
from pornarr_shared.config import get_settings

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class TranscodeCleanupResult:
    cache_bytes: int
    removed_directories: int
    removed_bytes: int
    evicted_directories: int


@dataclass(frozen=True, slots=True)
class _SegmentDirectory:
    path: Path
    session_id: UUID
    modified_at: float
    size: int


async def cleanup_transcodes(context: dict[str, Any]) -> dict[str, int]:
    """Remove old HLS orphans and cap the non-live transcode cache."""

    settings = get_settings()
    registry = TranscodeSessionRegistry(context["redis"], settings.transcode_path)
    live_session_ids = {session.id for session in await registry.live_sessions()}
    result = await asyncio.to_thread(
        cleanup_transcode_cache,
        settings.transcode_path,
        live_session_ids,
        min_age_seconds=settings.transcode_cleanup_min_age_seconds,
        max_bytes=settings.transcode_cache_max_bytes,
    )
    logger.info(
        "transcode cache cleanup: %d bytes remain; removed %d directories (%d evicted)",
        result.cache_bytes,
        result.removed_directories,
        result.evicted_directories,
    )
    return asdict(result)


def cleanup_transcode_cache(
    transcode_path: Path,
    live_session_ids: set[UUID],
    *,
    min_age_seconds: int,
    max_bytes: int,
    now: float | None = None,
) -> TranscodeCleanupResult:
    """Remove reclaimable HLS directories while preserving every live session."""

    current_time = time.time() if now is None else now
    directories = _segment_directories(transcode_path)
    remaining = sum(directory.size for directory in directories)
    removed_directories = 0
    removed_bytes = 0
    evicted_directories = 0

    for directory in directories:
        if directory.session_id in live_session_ids:
            continue
        if current_time - directory.modified_at < min_age_seconds:
            continue
        if _remove(directory.path):
            remaining -= directory.size
            removed_directories += 1
            removed_bytes += directory.size

    survivors = [directory for directory in directories if directory.path.exists()]
    for directory in sorted(survivors, key=lambda item: (item.modified_at, str(item.path))):
        if remaining <= max_bytes:
            break
        if directory.session_id in live_session_ids:
            continue
        if _remove(directory.path):
            remaining -= directory.size
            removed_directories += 1
            removed_bytes += directory.size
            evicted_directories += 1

    return TranscodeCleanupResult(
        cache_bytes=remaining,
        removed_directories=removed_directories,
        removed_bytes=removed_bytes,
        evicted_directories=evicted_directories,
    )


def _segment_directories(transcode_path: Path) -> list[_SegmentDirectory]:
    if not transcode_path.exists():
        return []
    directories: list[_SegmentDirectory] = []
    for path in transcode_path.iterdir():
        if not path.is_dir() or path.is_symlink():
            continue
        try:
            session_id = UUID(path.name)
            stat = path.stat()
        except (OSError, ValueError):
            continue
        directories.append(
            _SegmentDirectory(path, session_id, stat.st_mtime, _directory_size(path))
        )
    return directories


def _directory_size(directory: Path) -> int:
    total = 0
    for path in directory.rglob("*"):
        try:
            if path.is_file():
                total += path.stat().st_size
        except OSError:
            continue
    return total


def _remove(directory: Path) -> bool:
    shutil.rmtree(directory, ignore_errors=True)
    return not directory.exists()
