from __future__ import annotations

from pathlib import Path

from pornarr_media.hashing import SAMPLE_SIZE, oshash


def test_matches_the_opensubtitles_reference_algorithm(tmp_path: Path) -> None:
    path = tmp_path / "reference.bin"
    path.write_bytes(bytes(range(256)) * (SAMPLE_SIZE // 256 * 2))

    assert oshash(path) == "a0601fdf9f610000"


def test_small_file_returns_no_hash(tmp_path: Path) -> None:
    path = tmp_path / "small.bin"
    path.write_bytes(b"x" * (SAMPLE_SIZE - 1))

    assert oshash(path) is None
