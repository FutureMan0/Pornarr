"""Nightly cleanup of regenerable HLS segment directories."""

from __future__ import annotations

import os
import time
from pathlib import Path
from uuid import uuid4

from pornarr_worker.cleanup import cleanup_transcode_cache


def _segments(root: Path, session_id: str, *, size: int, modified_at: float) -> Path:
    directory = root / session_id
    directory.mkdir()
    (directory / "segment_00000.ts").write_bytes(b"x" * size)
    os.utime(directory, (modified_at, modified_at))
    return directory


def test_cleanup_removes_old_orphans_without_touching_live_sessions(tmp_path: Path) -> None:
    now = time.time()
    active_id = uuid4()
    active = _segments(tmp_path, str(active_id), size=8, modified_at=now - 600)
    orphan = _segments(tmp_path, str(uuid4()), size=8, modified_at=now - 600)
    fresh = _segments(tmp_path, str(uuid4()), size=8, modified_at=now - 30)

    result = cleanup_transcode_cache(
        tmp_path,
        {active_id},
        min_age_seconds=300,
        max_bytes=1024,
        now=now,
    )

    assert active.exists()
    assert not orphan.exists()
    assert fresh.exists()
    assert result.removed_directories == 1
    assert result.cache_bytes == 16


def test_cleanup_evicts_non_live_directories_oldest_first_to_enforce_the_cache_cap(
    tmp_path: Path,
) -> None:
    now = time.time()
    active_id = uuid4()
    active = _segments(tmp_path, str(active_id), size=8, modified_at=now - 10)
    oldest = _segments(tmp_path, str(uuid4()), size=8, modified_at=now - 30)
    newest = _segments(tmp_path, str(uuid4()), size=8, modified_at=now - 20)

    result = cleanup_transcode_cache(
        tmp_path,
        {active_id},
        min_age_seconds=300,
        max_bytes=16,
        now=now,
    )

    assert active.exists()
    assert not oldest.exists()
    assert newest.exists()
    assert result.cache_bytes == 16
    assert result.evicted_directories == 1
