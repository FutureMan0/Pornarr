from __future__ import annotations

import errno
import warnings
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

from pornarr_core import library_placement
from pornarr_core.library_placement import PlacementMethod, place_file


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


def test_cross_device_link_copies_and_warns_only_once_from_a_separate_mount(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    if not Path("/dev/shm").is_dir():
        pytest.skip("a separate tmpfs mount is required to test the cross-device fallback")
    monkeypatch.setattr(library_placement, "_copy_fallback_warned", False)
    first = tmp_path / "first.mkv"
    second = tmp_path / "second.mkv"
    first.write_bytes(b"first")
    second.write_bytes(b"second")

    with TemporaryDirectory(dir="/dev/shm", prefix="pornarr-import-") as directory:
        library_root = Path(directory)
        if first.stat().st_dev == library_root.stat().st_dev:
            pytest.skip("/dev/shm is not a separate filesystem in this environment")
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            first_placement = place_file(first, library_root, "Studio", "First", None)
            second_placement = place_file(second, library_root, "Studio", "Second", None)

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

    monkeypatch.setattr(library_placement.os, "link", failed_link)

    with pytest.raises(OSError, match="disk error"):
        place_file(source, tmp_path / "library", "Studio", "Title", "2024-01-01")

    assert not list((tmp_path / "library").rglob("*"))


def test_failed_copy_removes_the_partial_temporary_and_reservation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source.mkv"
    source.write_bytes(b"media")

    def cross_device_link(_: Path, __: Path) -> None:
        raise OSError(errno.EXDEV, "cross-device link")

    def interrupted_copy(_, output, *args, **kwargs) -> None:
        output.write(b"partial")
        raise OSError(errno.EIO, "copy interrupted")

    monkeypatch.setattr(library_placement.os, "link", cross_device_link)
    monkeypatch.setattr(library_placement.shutil, "copyfileobj", interrupted_copy)

    with pytest.raises(OSError, match="copy interrupted"):
        place_file(source, tmp_path / "library", "Studio", "Title", "2024-01-01")

    assert source.read_bytes() == b"media"
    assert not list((tmp_path / "library").rglob("*"))


def test_failed_promotion_removes_the_temporary_and_reservation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source.mkv"
    source.write_bytes(b"media")

    def failed_replace(_: Path, __: Path) -> None:
        raise OSError(errno.EIO, "promotion interrupted")

    monkeypatch.setattr(Path, "replace", failed_replace)

    with pytest.raises(OSError, match="promotion interrupted"):
        place_file(source, tmp_path / "library", "Studio", "Title", "2024-01-01")

    assert source.read_bytes() == b"media"
    assert not list((tmp_path / "library").rglob("*"))


def test_failed_source_removal_after_move_rolls_back_the_promoted_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source.mkv"
    source.write_bytes(b"media")
    original_unlink = Path.unlink

    def failed_source_unlink(path: Path, *args, **kwargs) -> None:
        if path == source:
            raise OSError(errno.EIO, "source removal interrupted")
        original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", failed_source_unlink)

    with pytest.raises(OSError, match="source removal interrupted"):
        place_file(source, tmp_path / "library", "Studio", "Title", "2024-01-01", move=True)

    assert source.read_bytes() == b"media"
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
