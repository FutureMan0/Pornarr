"""Small dependency-free DCT perceptual-hash primitives."""

from __future__ import annotations

import subprocess
from math import cos, pi
from pathlib import Path

FRAME_COUNT = 16
_SAMPLE_SIZE = 32
_HASH_SIZE = 8
_FRAME_BYTES = _SAMPLE_SIZE * _SAMPLE_SIZE


def frame_offsets(duration_seconds: float) -> tuple[float, ...]:
    """Return evenly spaced interior offsets, avoiding unstable first/last frames."""
    if duration_seconds <= 0:
        raise ValueError("media duration must be greater than zero")
    return tuple(
        duration_seconds * index / (FRAME_COUNT + 1) for index in range(1, FRAME_COUNT + 1)
    )


def hash_grayscale_frame(pixels: bytes, width: int, height: int) -> int:
    """Return a 64-bit low-frequency DCT hash for one grayscale frame."""
    if width <= 0 or height <= 0 or len(pixels) != width * height:
        raise ValueError("pixels must contain exactly width times height grayscale values")
    sample = _downsample(pixels, width, height)
    coefficients = [
        _coefficient(sample, horizontal, vertical)
        for vertical in range(_HASH_SIZE)
        for horizontal in range(_HASH_SIZE)
    ]
    median = sorted(coefficients[1:])[len(coefficients[1:]) // 2]
    result = 0
    for coefficient in coefficients[1:]:
        result = (result << 1) | int(coefficient > median)
    return result


def hamming_distance(first: int, second: int) -> int:
    """Count changed bits between two perceptual hashes."""
    if first < 0 or second < 0:
        raise ValueError("perceptual hashes must be non-negative")
    return (first ^ second).bit_count()


def perceptual_hash(source: Path) -> str:
    """Hash sixteen evenly-spaced FFmpeg frames into one stable hexadecimal value."""
    hashes = [
        hash_grayscale_frame(_frame(source, offset), _SAMPLE_SIZE, _SAMPLE_SIZE)
        for offset in frame_offsets(_duration_seconds(source))
    ]
    return f"{_majority_hash(hashes):016x}"


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
        return float(completed.stdout.strip())
    except ValueError as error:
        raise ValueError("ffprobe did not report a numeric duration") from error


def _frame(source: Path, offset_seconds: float) -> bytes:
    completed = _run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-nostdin",
            "-ss",
            f"{offset_seconds:.6f}",
            "-i",
            str(source),
            "-frames:v",
            "1",
            "-vf",
            f"scale={_SAMPLE_SIZE}:{_SAMPLE_SIZE},format=gray",
            "-f",
            "rawvideo",
            "-",
        ],
        text=False,
    )
    if len(completed.stdout) != _FRAME_BYTES:
        raise ValueError("ffmpeg did not produce one grayscale frame")
    return completed.stdout


def _majority_hash(hashes: list[int]) -> int:
    return sum(
        int(sum((value >> bit) & 1 for value in hashes) * 2 >= len(hashes)) << bit
        for bit in range(64)
    )


def _downsample(pixels: bytes, width: int, height: int) -> tuple[tuple[int, ...], ...]:
    return tuple(
        tuple(
            pixels[(row * height // _SAMPLE_SIZE) * width + column * width // _SAMPLE_SIZE]
            for column in range(_SAMPLE_SIZE)
        )
        for row in range(_SAMPLE_SIZE)
    )


def _coefficient(sample: tuple[tuple[int, ...], ...], horizontal: int, vertical: int) -> float:
    return sum(
        sample[row][column]
        * cos((2 * column + 1) * horizontal * pi / (2 * _SAMPLE_SIZE))
        * cos((2 * row + 1) * vertical * pi / (2 * _SAMPLE_SIZE))
        for row in range(_SAMPLE_SIZE)
        for column in range(_SAMPLE_SIZE)
    )


def _run(command: list[str], *, text: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(command, capture_output=True, check=True, text=text, timeout=120)
