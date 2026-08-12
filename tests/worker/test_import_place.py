from __future__ import annotations

import errno
import warnings
from pathlib import Path

import pytest

from pornarr_worker.jobs import import_place
from pornarr_worker.jobs.import_place import PlacementMethod, place_file


def test_place_file_hardlinks_on_the_same_filesystem(tmp_path: Path) -> None:
    source = tmp_path / "source.mkv"
    source.write_bytes(b"media")

    placed = place_file(
        source,
        tmp_path / "library",
        "Studio",
        "Title",
        "2024-01-01",
        quality="1080p",
    )

    assert placed.method is PlacementMethod.HARDLINK
    assert source.stat().st_ino == placed.path.stat().st_ino


def test_placement_collision_never_overwrites_an_existing_file(tmp_path: Path) -> None:
    source = tmp_path / "source.mkv"
    source.write_bytes(b"new")
    target = tmp_path / "library" / "Studio" / "2024" / "Title" / "unknown" / "Title.mkv"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"old")

    placed = place_file(source, tmp_path / "library", "Studio", "Title", "2024-01-01")

    assert target.read_bytes() == b"old"
    assert placed.path.name == "Title (1).mkv"


def test_cross_device_link_copies_and_warns_only_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def cross_device_link(_: Path, __: Path) -> None:
        raise OSError(errno.EXDEV, "cross-device link")

    monkeypatch.setattr(import_place.os, "link", cross_device_link)
    monkeypatch.setattr(import_place, "_copy_fallback_warned", False)
    first = tmp_path / "first.mkv"
    second = tmp_path / "second.mkv"
    first.write_bytes(b"first")
    second.write_bytes(b"second")

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        first_placement = place_file(first, tmp_path / "library", "Studio", "First", None)
        second_placement = place_file(second, tmp_path / "library", "Studio", "Second", None)

    assert first_placement.method is PlacementMethod.COPY
    assert second_placement.method is PlacementMethod.COPY
    assert len(caught) == 1
    assert "different filesystems" in str(caught[0].message)


def test_failed_placement_removes_the_reserved_destination(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source.mkv"
    source.write_bytes(b"media")

    def failed_link(_: Path, __: Path) -> None:
        raise OSError(errno.EIO, "disk error")

    monkeypatch.setattr(import_place.os, "link", failed_link)

    with pytest.raises(OSError, match="disk error"):
        place_file(source, tmp_path / "library", "Studio", "Title", "2024-01-01")

    assert not list((tmp_path / "library").rglob("*"))


def test_move_removes_the_usenet_source_after_a_successful_placement(tmp_path: Path) -> None:
    source = tmp_path / "source.mkv"
    source.write_bytes(b"media")

    placed = place_file(source, tmp_path / "library", "Studio", "Title", None, move=True)

    assert placed.method is PlacementMethod.MOVE
    assert not source.exists()
    assert placed.path.read_bytes() == b"media"


def test_empty_layout_is_rejected_instead_of_becoming_the_default(tmp_path: Path) -> None:
    source = tmp_path / "source.mkv"
    source.write_bytes(b"media")

    with pytest.raises(ValueError, match="non-empty"):
        place_file(source, tmp_path / "library", "Studio", "Title", None, layout="")
