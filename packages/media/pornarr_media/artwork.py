"""Generate deterministic poster and preview-frame artwork."""

from __future__ import annotations

import math
import subprocess
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from uuid import UUID

PREVIEW_FRAME_COUNT = 4
COMMAND_TIMEOUT_SECONDS = 120
POSTER_WIDTH = 640


class ArtworkState(StrEnum):
    READY = "ready"
    PLACEHOLDER = "placeholder"


@dataclass(frozen=True, slots=True)
class ArtworkPaths:
    directory: Path
    poster: Path
    frames: tuple[Path, ...]

    @classmethod
    def create(cls, thumbnail_path: Path, media_id: UUID, preview_frame_count: int) -> ArtworkPaths:
        directory = thumbnail_path / str(media_id)
        return cls(
            directory=directory,
            poster=directory / "poster.jpg",
            frames=tuple(
                directory / f"frame-{index:03}.jpg" for index in range(preview_frame_count)
            ),
        )


@dataclass(frozen=True, slots=True)
class Artwork:
    state: ArtworkState
    poster: Path
    frames: tuple[Path, ...]


def generate_artwork(
    source: Path,
    thumbnail_path: Path,
    media_id: UUID,
    *,
    preview_frame_count: int = PREVIEW_FRAME_COUNT,
) -> Artwork:
    """Regenerate one item's artwork, leaving a placeholder when it is unreadable."""
    if preview_frame_count <= 0:
        raise ValueError("preview frame count must be greater than zero")
    paths = ArtworkPaths.create(thumbnail_path, media_id, preview_frame_count)
    temporary_paths = ArtworkPaths(
        directory=paths.directory,
        poster=paths.directory / ".poster.tmp.jpg",
        frames=tuple(
            paths.directory / f".frame-{index:03}.tmp.jpg" for index in range(preview_frame_count)
        ),
    )
    try:
        duration_seconds = _duration_seconds(source)
        paths.directory.mkdir(parents=True, exist_ok=True)
        _run(
            build_frame_command(source, temporary_paths.poster, offset_seconds=duration_seconds / 2)
        )
        for index, temporary_frame in enumerate(temporary_paths.frames):
            offset_seconds = duration_seconds * (index + 1) / (preview_frame_count + 1)
            _run(build_frame_command(source, temporary_frame, offset_seconds=offset_seconds))
        for stale_frame in paths.directory.glob("frame-*.jpg"):
            stale_frame.unlink()
        temporary_paths.poster.replace(paths.poster)
        for temporary_frame, frame in zip(temporary_paths.frames, paths.frames, strict=True):
            temporary_frame.replace(frame)
    except (OSError, subprocess.SubprocessError, ValueError):
        _discard(temporary_paths)
        return _placeholder(paths)
    return Artwork(ArtworkState.READY, paths.poster, paths.frames)


def build_frame_command(source: Path, output: Path, *, offset_seconds: float) -> list[str]:
    """Build a seeked single-frame JPEG extraction command."""
    return [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-nostdin",
        "-y",
        "-ss",
        f"{offset_seconds:g}",
        "-i",
        str(source),
        "-vf",
        f"scale={POSTER_WIDTH}:-2",
        "-frames:v",
        "1",
        str(output),
    ]


def _placeholder(paths: ArtworkPaths) -> Artwork:
    paths.directory.mkdir(parents=True, exist_ok=True)
    for frame in paths.directory.glob("frame-*.jpg"):
        frame.unlink()
    paths.poster.unlink(missing_ok=True)
    placeholder = paths.directory / "placeholder.svg"
    placeholder.write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 9">'
        '<rect width="16" height="9" fill="#27272a"/>'
        '<path d="M6 3l4 1.5L6 6z" fill="#a1a1aa"/>'
        "</svg>"
    )
    return Artwork(ArtworkState.PLACEHOLDER, placeholder, ())


def _discard(paths: ArtworkPaths) -> None:
    paths.poster.unlink(missing_ok=True)
    for frame in paths.frames:
        frame.unlink(missing_ok=True)


def _duration_seconds(source: Path) -> float:
    completed = _run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(source),
        ]
    )
    try:
        duration_seconds = float(completed.stdout.strip())
    except ValueError as exc:
        raise ValueError("ffprobe did not report a numeric duration") from exc
    if not math.isfinite(duration_seconds) or duration_seconds <= 0:
        raise ValueError("media duration must be greater than zero")
    return duration_seconds


def _run(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        capture_output=True,
        check=True,
        text=True,
        timeout=COMMAND_TIMEOUT_SECONDS,
    )
