from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from pornarr_media import capabilities
from pornarr_media.capabilities import (
    HardwareAcceleration,
    VideoCodec,
    detect_hardware_capabilities,
)


def test_detects_a_working_nvenc_encoder_and_its_tested_resolution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def run(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        if command[-1] == "-encoders":
            return subprocess.CompletedProcess(
                command,
                0,
                " V....D h264_nvenc NVIDIA NVENC H.264 encoder\n"
                " V....D hevc_nvenc NVIDIA NVENC hevc encoder\n",
                "",
            )
        if command[0] == "nvidia-smi":
            return subprocess.CompletedProcess(command, 0, "NVIDIA GeForce RTX 3060\n", "")
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(capabilities.subprocess, "run", run)
    monkeypatch.setattr(capabilities, "_render_devices", lambda: ())

    report = detect_hardware_capabilities(timeout=4)

    assert report.nvidia_gpus == ("NVIDIA GeForce RTX 3060",)
    assert report.methods == (
        capabilities.HardwareCapability(
            acceleration=HardwareAcceleration.NVENC,
            codecs=(
                capabilities.CodecCapability(VideoCodec.H264, "7680x4320"),
                capabilities.CodecCapability(VideoCodec.HEVC, "7680x4320"),
            ),
            device=None,
        ),
    )
    assert report.software_only is False


def test_reports_a_listed_encoder_that_fails_a_real_encode_as_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def run(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        if command[-1] == "-encoders":
            return subprocess.CompletedProcess(command, 0, " V....D h264_nvenc NVENC\n", "")
        if command[0] == "nvidia-smi":
            return subprocess.CompletedProcess(command, 0, "GPU\n", "")
        return subprocess.CompletedProcess(command, 1, "", "No capable devices found")

    monkeypatch.setattr(capabilities.subprocess, "run", run)
    monkeypatch.setattr(capabilities, "_render_devices", lambda: ())

    report = detect_hardware_capabilities(timeout=4)

    assert report.methods == ()
    assert report.rejections == (
        capabilities.HardwareRejection(
            acceleration=HardwareAcceleration.NVENC,
            reason="all listed encoders failed a real encode",
        ),
        capabilities.HardwareRejection(
            acceleration=HardwareAcceleration.VAAPI,
            reason="no render device is available",
        ),
        capabilities.HardwareRejection(
            acceleration=HardwareAcceleration.QSV,
            reason="no render device is available",
        ),
    )
    assert report.software_only is True


def test_none_disables_hardware_detection_without_running_commands(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail(*_: object, **__: object) -> subprocess.CompletedProcess[str]:
        raise AssertionError("hardware detection should be disabled")

    monkeypatch.setattr(capabilities.subprocess, "run", fail)

    report = detect_hardware_capabilities(requested="none")

    assert report.methods == ()
    assert report.rejections == (
        capabilities.HardwareRejection(
            acceleration=None,
            reason="hardware acceleration is disabled by configuration",
        ),
    )
    assert report.software_only is True


def test_vaapi_uses_the_render_device_and_a_bounded_command_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[list[str], float]] = []
    device = Path("/dev/dri/renderD128")

    def run(command: list[str], *, timeout: float, **_: object) -> subprocess.CompletedProcess[str]:
        calls.append((command, timeout))
        if command[-1] == "-encoders":
            return subprocess.CompletedProcess(command, 0, " V....D h264_vaapi VAAPI\n", "")
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(capabilities.subprocess, "run", run)
    monkeypatch.setattr(capabilities, "_render_devices", lambda: (device,))

    report = detect_hardware_capabilities(requested="vaapi", timeout=4)

    assert report.methods == (
        capabilities.HardwareCapability(
            acceleration=HardwareAcceleration.VAAPI,
            codecs=(capabilities.CodecCapability(VideoCodec.H264, "7680x4320"),),
            device=device,
        ),
    )
    assert "vaapi=va:/dev/dri/renderD128" in calls[1][0]
    assert all(timeout <= 1 for _, timeout in calls)


def test_qsv_records_the_highest_resolution_that_completes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    device = Path("/dev/dri/renderD128")

    def run(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        if command[-1] == "-encoders":
            return subprocess.CompletedProcess(command, 0, " V....D h264_qsv QSV\n", "")
        if "color=c=black:s=7680x4320:r=1" in command:
            return subprocess.CompletedProcess(command, 1, "", "unsupported resolution")
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(capabilities.subprocess, "run", run)
    monkeypatch.setattr(capabilities, "_render_devices", lambda: (device,))

    report = detect_hardware_capabilities(requested="qsv", timeout=4)

    assert report.methods[0].codecs == (capabilities.CodecCapability(VideoCodec.H264, "3840x2160"),)


def test_missing_ffmpeg_leaves_an_explicit_software_only_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def missing(*_: object, **__: object) -> subprocess.CompletedProcess[str]:
        raise FileNotFoundError

    monkeypatch.setattr(capabilities.subprocess, "run", missing)
    monkeypatch.setattr(capabilities, "_render_devices", lambda: ())

    report = detect_hardware_capabilities(requested="nvenc", timeout=4)

    assert report.methods == ()
    assert report.nvidia_gpus == ()
    assert report.rejections == (
        capabilities.HardwareRejection(
            HardwareAcceleration.NVENC, "no supported encoder is listed by ffmpeg"
        ),
    )


def test_render_device_discovery_and_expired_commands_are_safe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    device = Path("/dev/dri/renderD128")
    monkeypatch.setattr(Path, "glob", lambda *_: [device])

    assert capabilities._render_devices() == (device,)
    assert capabilities._run(["ffmpeg"], timeout=0) is None
