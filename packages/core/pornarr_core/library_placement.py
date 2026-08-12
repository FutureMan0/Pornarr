"""Atomic non-destructive placement of imported media files."""

from __future__ import annotations

import errno
import os
import shutil
import warnings
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from pornarr_core.library_naming import DEFAULT_LAYOUT, build_library_path

_copy_fallback_warned = False


class PlacementMethod(StrEnum):
    HARDLINK = "hardlink"
    COPY = "copy"
    MOVE = "move"


@dataclass(frozen=True)
class Placement:
    path: Path
    method: PlacementMethod


def place_file(
    source: Path,
    library_root: Path,
    studio: str | None,
    title: str,
    release_date: str | None,
    *,
    layout: str | None = None,
    quality: str | None = None,
    move: bool = False,
) -> Placement:
    """Place a file with an atomic collision reservation and full rollback."""
    relative = build_library_path(
        studio,
        title,
        release_date,
        source.suffix,
        DEFAULT_LAYOUT if layout is None else layout,
        quality,
    )
    target = _reserve_path(library_root / relative)
    temporary: Path | None = None
    try:
        if move:
            temporary = _copy_to_temporary(source, target)
            method = PlacementMethod.MOVE
        else:
            try:
                temporary = _hardlink_to_temporary(source, target)
                method = PlacementMethod.HARDLINK
            except OSError as error:
                if error.errno != errno.EXDEV:
                    raise
                temporary = _copy_to_temporary(source, target)
                method = PlacementMethod.COPY
                _warn_copy_fallback_once()
        temporary.replace(target)
        if move:
            source.unlink()
    except Exception:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
        target.unlink(missing_ok=True)
        _remove_empty_parents(target.parent, library_root)
        raise
    return Placement(path=target, method=method)


def _reserve_path(target: Path) -> Path:
    target.parent.mkdir(parents=True, exist_ok=True)
    for index in range(1, 10_000):
        candidate = target if index == 1 else target.with_stem(f"{target.stem} ({index - 1})")
        try:
            descriptor = os.open(candidate, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            continue
        else:
            os.close(descriptor)
            return candidate
    raise FileExistsError("could not find a free library filename")


def _hardlink_to_temporary(source: Path, target: Path) -> Path:
    for index in range(1, 10_000):
        temporary = target.with_name(f".{target.name}.{index}.tmp")
        try:
            os.link(source, temporary)
        except FileExistsError:
            continue
        return temporary
    raise FileExistsError("could not create a temporary library filename")


def _copy_to_temporary(source: Path, target: Path) -> Path:
    for index in range(1, 10_000):
        temporary = target.with_name(f".{target.name}.{index}.tmp")
        try:
            descriptor = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            continue
        try:
            with source.open("rb") as input_file, os.fdopen(descriptor, "wb") as output_file:
                shutil.copyfileobj(input_file, output_file)
            shutil.copystat(source, temporary)
        except Exception:
            temporary.unlink(missing_ok=True)
            raise
        return temporary
    raise FileExistsError("could not create a temporary library filename")


def _warn_copy_fallback_once() -> None:
    global _copy_fallback_warned
    if not _copy_fallback_warned:
        warnings.warn(
            "library and download paths are on different filesystems; imports will copy",
            RuntimeWarning,
            stacklevel=2,
        )
        _copy_fallback_warned = True


def _remove_empty_parents(path: Path, root: Path) -> None:
    while path != root:
        try:
            path.rmdir()
        except OSError:
            return
        path = path.parent
