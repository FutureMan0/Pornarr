"""OpenSubtitles-compatible file fingerprints."""

from __future__ import annotations

from pathlib import Path

SAMPLE_SIZE = 64 * 1024
_MASK = (1 << 64) - 1


def oshash(path: Path) -> str | None:
    """Return the OpenSubtitles hash, or None when a file is too small to sample."""
    size = path.stat().st_size
    if size < SAMPLE_SIZE:
        return None
    with path.open("rb") as handle:
        first = handle.read(SAMPLE_SIZE)
        handle.seek(-SAMPLE_SIZE, 2)
        last = handle.read(SAMPLE_SIZE)
    total = size + _word_sum(first) + _word_sum(last)
    return f"{total & _MASK:016x}"


def _word_sum(sample: bytes) -> int:
    return sum(
        int.from_bytes(sample[offset : offset + 8], "little") for offset in range(0, len(sample), 8)
    )
