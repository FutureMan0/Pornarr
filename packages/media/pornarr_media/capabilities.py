"""Bounded runtime detection for hardware video encoders."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from time import monotonic
from typing import Literal


class HardwareAcceleration(StrEnum):
    NVENC = "nvenc"
    VAAPI = "vaapi"
    QSV = "qsv"


class VideoCodec(StrEnum):
    H264 = "h264"
    HEVC = "hevc"
    AV1 = "av1"


@dataclass(frozen=True, slots=True)
class CodecCapability:
    codec: VideoCodec
    maximum_tested_resolution: str


@dataclass(frozen=True, slots=True)
class HardwareCapability:
    acceleration: HardwareAcceleration
    codecs: tuple[CodecCapability, ...]
    device: Path | None


@dataclass(frozen=True, slots=True)
class HardwareRejection:
    acceleration: HardwareAcceleration | None
    reason: str


@dataclass(frozen=True, slots=True)
class HardwareCapabilities:
    methods: tuple[HardwareCapability, ...]
    rejections: tuple[HardwareRejection, ...]
    nvidia_gpus: tuple[str, ...]

    @property
    def software_only(self) -> bool:
        return not self.methods


RequestedAcceleration = Literal["auto", "nvenc", "vaapi", "qsv", "none"]

_ACCELERATIONS = tuple(HardwareAcceleration)
_CODECS = tuple(VideoCodec)
_ENCODERS = {
    acceleration: {codec: f"{codec.value}_{acceleration.value}" for codec in _CODECS}
    for acceleration in _ACCELERATIONS
}
_RESOLUTIONS = ("7680x4320", "3840x2160", "1920x1080")
_COMMAND_TIMEOUT_SECONDS = 1.0


def detect_hardware_capabilities(
    *, requested: RequestedAcceleration = "auto", timeout: float = 4.0
) -> HardwareCapabilities:
    """Return working encoders, with an explicit reason for every rejection.

    A listed FFmpeg encoder is only a possibility: each candidate must encode a
    generated frame. The shared deadline keeps startup bounded even when a
    container exposes a broken GPU device.
    """
    if requested == "none":
        return HardwareCapabilities(
            methods=(),
            rejections=(
                HardwareRejection(None, "hardware acceleration is disabled by configuration"),
            ),
            nvidia_gpus=(),
        )

    deadline = monotonic() + timeout
    encoders = _available_encoders(_remaining_timeout(deadline))
    selected = _ACCELERATIONS if requested == "auto" else (HardwareAcceleration(requested),)
    devices = _render_devices()
    device = devices[0] if devices else None
    nvidia_gpus = _nvidia_gpus(deadline) if HardwareAcceleration.NVENC in selected else ()
    methods: list[HardwareCapability] = []
    rejections: list[HardwareRejection] = []

    for acceleration in selected:
        if (
            acceleration in {HardwareAcceleration.VAAPI, HardwareAcceleration.QSV}
            and device is None
        ):
            rejections.append(HardwareRejection(acceleration, "no render device is available"))
            continue
        available = {
            codec: encoder
            for codec, encoder in _ENCODERS[acceleration].items()
            if encoder in encoders
        }
        if not available:
            rejections.append(
                HardwareRejection(acceleration, "no supported encoder is listed by ffmpeg")
            )
            continue
        codecs = tuple(
            capability
            for codec, encoder in available.items()
            if (capability := _probe_codec(acceleration, codec, encoder, device, deadline))
            is not None
        )
        if codecs:
            methods.append(HardwareCapability(acceleration, codecs, device))
        else:
            rejections.append(
                HardwareRejection(acceleration, "all listed encoders failed a real encode")
            )

    return HardwareCapabilities(tuple(methods), tuple(rejections), nvidia_gpus)


def _available_encoders(timeout: float) -> frozenset[str]:
    completed = _run(["ffmpeg", "-hide_banner", "-encoders"], timeout)
    if completed is None or completed.returncode != 0:
        return frozenset()
    return frozenset(
        parts[1]
        for line in completed.stdout.splitlines()
        if len(parts := line.split()) >= 2 and parts[1] in _encoder_names()
    )


def _encoder_names() -> frozenset[str]:
    return frozenset(encoder for codecs in _ENCODERS.values() for encoder in codecs.values())


def _probe_codec(
    acceleration: HardwareAcceleration,
    codec: VideoCodec,
    encoder: str,
    device: Path | None,
    deadline: float,
) -> CodecCapability | None:
    for resolution in _RESOLUTIONS:
        if _encode(acceleration, encoder, device, resolution, deadline):
            return CodecCapability(codec, resolution)
    return None


def _encode(
    acceleration: HardwareAcceleration,
    encoder: str,
    device: Path | None,
    resolution: str,
    deadline: float,
) -> bool:
    timeout = _remaining_timeout(deadline)
    if timeout <= 0:
        return False
    command = ["ffmpeg", "-v", "error"]
    if acceleration == HardwareAcceleration.VAAPI:
        assert device is not None
        command.extend(["-init_hw_device", f"vaapi=va:{device}", "-filter_hw_device", "va"])
    elif acceleration == HardwareAcceleration.QSV:
        assert device is not None
        command.extend(["-init_hw_device", f"qsv=hw:{device}", "-filter_hw_device", "hw"])
    command.extend(
        [
            "-f",
            "lavfi",
            "-i",
            f"color=c=black:s={resolution}:r=1",
            "-frames:v",
            "1",
        ]
    )
    if acceleration in {HardwareAcceleration.VAAPI, HardwareAcceleration.QSV}:
        command.extend(["-vf", "format=nv12,hwupload"])
    command.extend(["-c:v", encoder, "-f", "null", "-"])
    completed = _run(command, timeout)
    return completed is not None and completed.returncode == 0


def _nvidia_gpus(deadline: float) -> tuple[str, ...]:
    completed = _run(
        ["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"], _remaining_timeout(deadline)
    )
    if completed is None or completed.returncode != 0:
        return ()
    return tuple(line.strip() for line in completed.stdout.splitlines() if line.strip())


def _render_devices() -> tuple[Path, ...]:
    return tuple(sorted(Path("/dev/dri").glob("renderD*")))


def _remaining_timeout(deadline: float) -> float:
    return max(0, min(_COMMAND_TIMEOUT_SECONDS, deadline - monotonic()))


def _run(command: list[str], timeout: float) -> subprocess.CompletedProcess[str] | None:
    if timeout <= 0:
        return None
    try:
        return subprocess.run(command, capture_output=True, text=True, timeout=timeout, check=False)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
