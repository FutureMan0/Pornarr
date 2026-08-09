"""FFmpeg-backed HLS transcoding for one playback session."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

from pornarr_media.capabilities import HardwareAcceleration

HLS_SEGMENT_SECONDS = 4


@dataclass(frozen=True, slots=True)
class HlsPaths:
    """The HLS assets belonging to one transcode session."""

    directory: Path
    master_playlist: Path
    variant_playlist: Path
    segment_pattern: Path

    @classmethod
    def create(cls, transcode_path: Path, session_id: UUID) -> HlsPaths:
        directory = transcode_path / str(session_id)
        return cls(
            directory=directory,
            master_playlist=directory / "master.m3u8",
            variant_playlist=directory / "variant.m3u8",
            segment_pattern=directory / "segment_%05d.ts",
        )


@dataclass(slots=True)
class HlsTranscode:
    """A running FFmpeg process and the files it writes."""

    paths: HlsPaths
    process: asyncio.subprocess.Process

    async def stop(self, timeout: float = 5) -> None:
        """Terminate FFmpeg, escalating to kill if it does not exit promptly."""
        if self.process.returncode is not None:
            return
        try:
            self.process.terminate()
        except ProcessLookupError:
            return
        try:
            await asyncio.wait_for(self.process.wait(), timeout)
        except TimeoutError:
            self.process.kill()
            await self.process.wait()


def build_hls_command(
    *,
    source: Path,
    paths: HlsPaths,
    acceleration: HardwareAcceleration | None = None,
    device: Path | None = None,
    start_offset_seconds: float = 0,
    subtitle_passthrough: bool = False,
) -> list[str]:
    """Build a seekable H.264/AAC HLS encode command for one source file."""
    if start_offset_seconds < 0:
        raise ValueError("start offset must not be negative")

    setup, encoder, filter_args = _video_arguments(acceleration, device)
    command = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y", *setup]
    if start_offset_seconds:
        command.extend(["-ss", str(start_offset_seconds)])
    command.extend(
        [
            "-i",
            str(source),
            "-map",
            "0:v:0",
            "-map",
            "0:a:0?",
            "-map",
            "0:s:0?",
            *filter_args,
            "-c:v",
            encoder,
            "-c:a",
            "aac",
            "-ac",
            "2",
            "-c:s",
            "copy" if subtitle_passthrough else "webvtt",
            "-f",
            "hls",
            "-hls_time",
            str(HLS_SEGMENT_SECONDS),
            "-hls_list_size",
            "0",
            "-hls_playlist_type",
            "event",
            "-hls_flags",
            "independent_segments+temp_file",
            "-hls_segment_filename",
            str(paths.segment_pattern),
            "-master_pl_name",
            paths.master_playlist.name,
            str(paths.variant_playlist),
        ]
    )
    return command


async def start_hls_transcode(
    source: Path,
    transcode_path: Path,
    session_id: UUID,
    *,
    acceleration: HardwareAcceleration | None = None,
    device: Path | None = None,
    start_offset_seconds: float = 0,
    subtitle_passthrough: bool = False,
) -> HlsTranscode:
    """Create a session directory and start FFmpeg without blocking the event loop."""
    paths = HlsPaths.create(transcode_path, session_id)
    paths.directory.mkdir(parents=True)
    process = await asyncio.create_subprocess_exec(
        *build_hls_command(
            source=source,
            paths=paths,
            acceleration=acceleration,
            device=device,
            start_offset_seconds=start_offset_seconds,
            subtitle_passthrough=subtitle_passthrough,
        ),
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    return HlsTranscode(paths, process)


def _video_arguments(
    acceleration: HardwareAcceleration | None, device: Path | None
) -> tuple[list[str], str, list[str]]:
    if acceleration is None:
        return [], "libx264", []
    if acceleration == HardwareAcceleration.NVENC:
        return [], "h264_nvenc", []
    if device is None:
        raise ValueError(f"{acceleration.value} requires a render device")
    if acceleration == HardwareAcceleration.VAAPI:
        return (
            ["-init_hw_device", f"vaapi=va:{device}", "-filter_hw_device", "va"],
            "h264_vaapi",
            ["-vf", "format=nv12,hwupload"],
        )
    return (
        ["-init_hw_device", f"qsv=hw:{device}", "-filter_hw_device", "hw"],
        "h264_qsv",
        ["-vf", "format=nv12,hwupload"],
    )
