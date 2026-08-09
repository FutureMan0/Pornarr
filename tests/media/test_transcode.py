from __future__ import annotations

import asyncio
import subprocess
import sys
from pathlib import Path
from shutil import which
from uuid import uuid4

import pytest

from pornarr_media.capabilities import HardwareAcceleration
from pornarr_media.transcode import (
    HlsPaths,
    HlsTranscode,
    build_hls_command,
    start_hls_transcode,
)

FFMPEG_AVAILABLE = which("ffmpeg") is not None


@pytest.mark.parametrize(
    ("acceleration", "expected_encoder"),
    [
        (None, "libx264"),
        (HardwareAcceleration.NVENC, "h264_nvenc"),
        (HardwareAcceleration.VAAPI, "h264_vaapi"),
        (HardwareAcceleration.QSV, "h264_qsv"),
    ],
)
def test_build_hls_command_selects_an_encoder_per_acceleration_method(
    acceleration: HardwareAcceleration | None, expected_encoder: str, tmp_path: Path
) -> None:
    paths = HlsPaths.create(tmp_path, uuid4())

    command = build_hls_command(
        source=Path("/library/source.mkv"),
        paths=paths,
        acceleration=acceleration,
        device=Path("/dev/dri/renderD128"),
    )

    assert command[0] == "ffmpeg"
    assert command[command.index("-c:v") + 1] == expected_encoder
    assert command[command.index("-c:a") + 1] == "aac"
    assert command[command.index("-ac") + 1] == "2"
    assert command[command.index("-master_pl_name") + 1] == "master.m3u8"
    assert command[-1] == str(paths.variant_playlist)


@pytest.mark.parametrize("acceleration", [HardwareAcceleration.VAAPI, HardwareAcceleration.QSV])
def test_hardware_upload_methods_require_a_device(
    acceleration: HardwareAcceleration, tmp_path: Path
) -> None:
    with pytest.raises(ValueError, match="requires a render device"):
        build_hls_command(
            source=Path("/library/source.mkv"),
            paths=HlsPaths.create(tmp_path, uuid4()),
            acceleration=acceleration,
        )


def test_hardware_commands_upload_frames_to_the_selected_device(tmp_path: Path) -> None:
    device = Path("/dev/dri/renderD128")

    vaapi = build_hls_command(
        source=Path("/library/source.mkv"),
        paths=HlsPaths.create(tmp_path, uuid4()),
        acceleration=HardwareAcceleration.VAAPI,
        device=device,
    )
    qsv = build_hls_command(
        source=Path("/library/source.mkv"),
        paths=HlsPaths.create(tmp_path, uuid4()),
        acceleration=HardwareAcceleration.QSV,
        device=device,
    )

    assert f"vaapi=va:{device}" in vaapi
    assert "format=nv12,hwupload" in vaapi
    assert f"qsv=hw:{device}" in qsv
    assert "format=nv12,hwupload" in qsv


def test_hls_command_uses_the_seek_offset_and_converts_incompatible_subtitles(
    tmp_path: Path,
) -> None:
    command = build_hls_command(
        source=Path("/library/source.mkv"),
        paths=HlsPaths.create(tmp_path, uuid4()),
        start_offset_seconds=12.5,
    )

    assert command[command.index("-ss") + 1] == "12.5"
    assert command.index("-ss") < command.index("-i")
    assert command[command.index("-c:s") + 1] == "webvtt"


def test_hls_command_copies_webvtt_subtitles(tmp_path: Path) -> None:
    command = build_hls_command(
        source=Path("/library/source.mkv"),
        paths=HlsPaths.create(tmp_path, uuid4()),
        subtitle_passthrough=True,
    )

    assert command[command.index("-c:s") + 1] == "copy"


@pytest.mark.skipif(not FFMPEG_AVAILABLE, reason="ffmpeg is required")
async def test_software_transcode_creates_a_master_playlist_variant_and_segment(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=s=320x180:r=24:d=1",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=1000:d=1",
            "-map",
            "0:v:0",
            "-map",
            "1:a:0",
            "-c:v",
            "libx264",
            "-c:a",
            "aac",
            "-ac",
            "2",
            str(source),
        ],
        check=True,
    )

    transcode = await start_hls_transcode(source, tmp_path / "transcodes", uuid4())
    await asyncio.wait_for(transcode.process.wait(), timeout=5)

    assert transcode.process.returncode == 0
    assert "variant.m3u8" in transcode.paths.master_playlist.read_text()
    assert transcode.paths.variant_playlist.exists()
    assert list(transcode.paths.directory.glob("segment_*.ts"))


async def test_stop_terminates_an_active_process_within_the_timeout(tmp_path: Path) -> None:
    process = await asyncio.create_subprocess_exec(
        sys.executable, "-c", "import time; time.sleep(60)"
    )
    transcode = HlsTranscode(HlsPaths.create(tmp_path, uuid4()), process)

    await transcode.stop(timeout=0.1)

    assert process.returncode is not None
