"""Generate tiled hover-preview sprites and their WebVTT indexes."""

from __future__ import annotations

import math
import subprocess
from dataclasses import dataclass
from pathlib import Path

DEFAULT_INTERVAL_SECONDS = 10
DEFAULT_TILE_WIDTH = 160
DEFAULT_TILE_HEIGHT = 90
DEFAULT_COLUMNS = 10
COMMAND_TIMEOUT_SECONDS = 120


@dataclass(frozen=True, slots=True)
class PreviewSpriteOptions:
    """Sampling and layout choices for one preview sprite."""

    interval_seconds: float = DEFAULT_INTERVAL_SECONDS
    tile_width: int = DEFAULT_TILE_WIDTH
    tile_height: int = DEFAULT_TILE_HEIGHT
    columns: int = DEFAULT_COLUMNS

    def __post_init__(self) -> None:
        if self.interval_seconds <= 0:
            raise ValueError("interval must be greater than zero")
        if self.tile_width <= 0 or self.tile_height <= 0:
            raise ValueError("tile dimensions must be greater than zero")
        if self.columns <= 0:
            raise ValueError("columns must be greater than zero")


@dataclass(frozen=True, slots=True)
class PreviewSprite:
    """The generated image and VTT index for a media file."""

    image: Path
    vtt: Path


DEFAULT_OPTIONS = PreviewSpriteOptions()


def generate_preview_sprite(
    source: Path,
    output_directory: Path,
    *,
    options: PreviewSpriteOptions = DEFAULT_OPTIONS,
) -> PreviewSprite:
    """Generate a tiled JPEG and WebVTT map for a background worker job."""
    duration_seconds = _duration_seconds(source)
    frame_count = math.ceil(duration_seconds / options.interval_seconds)
    if frame_count <= 0:
        raise ValueError("media duration must be greater than zero")

    output_directory.mkdir(parents=True, exist_ok=True)
    image = output_directory / "sprite.jpg"
    vtt = output_directory / "sprite.vtt"
    temporary_image = output_directory / ".sprite.tmp.jpg"
    temporary_vtt = output_directory / ".sprite.tmp.vtt"
    _run(build_sprite_command(source, temporary_image, frame_count=frame_count, options=options))
    temporary_vtt.write_text(
        build_webvtt(duration_seconds=duration_seconds, image_name=image.name, options=options)
    )
    temporary_image.replace(image)
    temporary_vtt.replace(vtt)
    return PreviewSprite(image=image, vtt=vtt)


def try_generate_preview_sprite(
    source: Path,
    output_directory: Path,
    *,
    options: PreviewSpriteOptions = DEFAULT_OPTIONS,
) -> PreviewSprite | None:
    """Return no preview when a background generation attempt fails."""
    try:
        return generate_preview_sprite(source, output_directory, options=options)
    except (OSError, subprocess.SubprocessError, ValueError):
        return None


def build_sprite_command(
    source: Path,
    output: Path,
    *,
    frame_count: int,
    options: PreviewSpriteOptions,
) -> list[str]:
    """Build one FFmpeg invocation that samples and tiles every preview frame."""
    rows = math.ceil(frame_count / options.columns)
    filter_graph = (
        f"fps=1/{options.interval_seconds:g},scale={options.tile_width}:{options.tile_height},"
        f"tile={options.columns}x{rows}:padding=0:margin=0"
    )
    return [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-nostdin",
        "-y",
        "-i",
        str(source),
        "-vf",
        filter_graph,
        "-frames:v",
        "1",
        str(output),
    ]


def build_webvtt(
    *,
    duration_seconds: float,
    image_name: str,
    options: PreviewSpriteOptions,
) -> str:
    """Map each sampled time interval to the corresponding sprite tile."""
    frame_count = math.ceil(duration_seconds / options.interval_seconds)
    cues = ["WEBVTT"]
    for index in range(frame_count):
        start = index * options.interval_seconds
        end = min(start + options.interval_seconds, duration_seconds)
        column = index % options.columns
        row = index // options.columns
        x = column * options.tile_width
        y = row * options.tile_height
        cues.extend(
            [
                "",
                f"{_timestamp(start)} --> {_timestamp(end)}",
                f"{image_name}#xywh={x},{y},{options.tile_width},{options.tile_height}",
            ]
        )
    return "\n".join(cues) + "\n"


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


def _timestamp(seconds: float) -> str:
    milliseconds = round(seconds * 1000)
    hours, milliseconds = divmod(milliseconds, 3_600_000)
    minutes, milliseconds = divmod(milliseconds, 60_000)
    seconds, milliseconds = divmod(milliseconds, 1_000)
    return f"{hours:02}:{minutes:02}:{seconds:02}.{milliseconds:03}"
