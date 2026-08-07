"""Bounded ffprobe extraction for physical media files."""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class MediaProbeError(Exception):
    """ffprobe could not parse the media file."""


class MediaProbeTimeoutError(MediaProbeError):
    """ffprobe exceeded its bounded execution time."""


@dataclass(frozen=True, slots=True)
class ProbeResult:
    resolution: str | None
    codecs: tuple[str, ...]
    duration: float | None
    bitrate: int | None
    streams: tuple[dict[str, object], ...]
    container: str | None


def probe(path: Path, *, timeout: float = 30) -> ProbeResult:
    """Extract display and decision metadata without trusting the input file."""
    command = ["ffprobe", "-v", "error", "-show_format", "-show_streams", "-of", "json", str(path)]
    try:
        completed = subprocess.run(
            command, capture_output=True, text=True, timeout=timeout, check=False
        )
    except subprocess.TimeoutExpired as error:
        raise MediaProbeTimeoutError(f"ffprobe timed out after {timeout} seconds") from error
    if completed.returncode != 0:
        raise MediaProbeError("Media file could not be probed")
    try:
        document = json.loads(completed.stdout)
        format_data = document["format"]
        streams = tuple(document.get("streams", []))
    except (json.JSONDecodeError, KeyError, TypeError) as error:
        raise MediaProbeError("Media file could not be probed") from error
    return _result(format_data, streams)


def _result(format_data: dict[str, Any], streams: tuple[dict[str, Any], ...]) -> ProbeResult:
    video = next((stream for stream in streams if stream.get("codec_type") == "video"), None)
    resolution = None if video is None else f"{video.get('width')}x{video.get('height')}"
    codecs = tuple(str(stream["codec_name"]) for stream in streams if "codec_name" in stream)
    return ProbeResult(
        resolution=resolution,
        codecs=codecs,
        duration=_float(format_data.get("duration")),
        bitrate=_integer(format_data.get("bit_rate")),
        streams=tuple(dict(stream) for stream in streams),
        container=_string(format_data.get("format_name")),
    )


def _float(value: object) -> float | None:
    if not isinstance(value, (str, int, float)):
        return None
    try:
        return float(value)
    except ValueError:
        return None


def _integer(value: object) -> int | None:
    if not isinstance(value, (str, int, float)):
        return None
    try:
        return int(value)
    except ValueError:
        return None


def _string(value: object) -> str | None:
    return value if isinstance(value, str) else None
