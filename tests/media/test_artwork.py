from __future__ import annotations

import subprocess
from pathlib import Path
from shutil import which
from uuid import uuid4

import pytest

from pornarr_media.artwork import (
    ArtworkPaths,
    ArtworkState,
    build_frame_command,
    generate_artwork,
)

FFMPEG_AVAILABLE = which("ffmpeg") is not None and which("ffprobe") is not None


def test_artwork_paths_are_deterministic_per_media_item(tmp_path: Path) -> None:
    media_id = uuid4()

    paths = ArtworkPaths.create(tmp_path, media_id, preview_frame_count=3)

    assert paths.directory == tmp_path / str(media_id)
    assert paths.poster == paths.directory / "poster.jpg"
    assert paths.frames == tuple(paths.directory / f"frame-{index:03}.jpg" for index in range(3))


def test_frame_command_seeks_before_decoding_and_writes_one_jpeg(tmp_path: Path) -> None:
    output = tmp_path / "poster.tmp.jpg"

    command = build_frame_command(Path("/library/source.mkv"), output, offset_seconds=12.5)

    assert command[command.index("-ss") + 1] == "12.5"
    assert command.index("-ss") < command.index("-i")
    assert command[command.index("-frames:v") + 1] == "1"
    assert command[-1] == str(output)


def test_unreadable_source_creates_a_placeholder_with_an_explicit_state(tmp_path: Path) -> None:
    artwork = generate_artwork(tmp_path / "missing.mkv", tmp_path / "thumbnails", uuid4())

    assert artwork.state == ArtworkState.PLACEHOLDER
    assert artwork.poster.name == "placeholder.svg"
    assert artwork.poster.read_text().startswith("<svg")
    assert artwork.frames == ()


@pytest.mark.skipif(not FFMPEG_AVAILABLE, reason="ffmpeg and ffprobe are required")
def test_artwork_generation_replaces_prior_frames_without_accumulating(tmp_path: Path) -> None:
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
            "-c:v",
            "libx264",
            str(source),
        ],
        check=True,
    )
    thumbnails = tmp_path / "thumbnails"
    media_id = uuid4()

    first = generate_artwork(source, thumbnails, media_id, preview_frame_count=4)
    second = generate_artwork(source, thumbnails, media_id, preview_frame_count=2)

    assert first.state == ArtworkState.READY
    assert second.state == ArtworkState.READY
    assert second.poster == thumbnails / str(media_id) / "poster.jpg"
    assert len(second.frames) == 2
    assert sorted(path.name for path in second.poster.parent.glob("frame-*.jpg")) == [
        "frame-000.jpg",
        "frame-001.jpg",
    ]
