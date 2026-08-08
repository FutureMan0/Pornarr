from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

import pornarr_media.sprites as sprites
from pornarr_media.sprites import (
    PreviewSpriteOptions,
    build_sprite_command,
    build_webvtt,
    generate_preview_sprite,
    try_generate_preview_sprite,
)


def test_preview_sprite_options_reject_invalid_dimensions_and_intervals() -> None:
    with pytest.raises(ValueError, match="interval"):
        PreviewSpriteOptions(interval_seconds=0)
    with pytest.raises(ValueError, match="tile"):
        PreviewSpriteOptions(tile_width=0)
    with pytest.raises(ValueError, match="columns"):
        PreviewSpriteOptions(columns=0)


def test_webvtt_maps_each_interval_to_its_sprite_tile() -> None:
    rendered = build_webvtt(
        duration_seconds=22,
        image_name="sprite.jpg",
        options=PreviewSpriteOptions(
            interval_seconds=10,
            tile_width=160,
            tile_height=90,
            columns=2,
        ),
    )

    assert rendered == (
        "WEBVTT\n\n"
        "00:00:00.000 --> 00:00:10.000\n"
        "sprite.jpg#xywh=0,0,160,90\n\n"
        "00:00:10.000 --> 00:00:20.000\n"
        "sprite.jpg#xywh=160,0,160,90\n\n"
        "00:00:20.000 --> 00:00:22.000\n"
        "sprite.jpg#xywh=0,90,160,90\n"
    )


def test_sprite_command_samples_frames_and_tiles_them(tmp_path: Path) -> None:
    output = tmp_path / "sprite.tmp.jpg"
    command = build_sprite_command(
        Path("/library/source.mkv"),
        output,
        frame_count=12,
        options=PreviewSpriteOptions(interval_seconds=5, tile_width=160, tile_height=90, columns=4),
    )

    assert command[:3] == ["ffmpeg", "-hide_banner", "-loglevel"]
    assert command[command.index("-vf") + 1] == "fps=1/5,scale=160:90,tile=4x3:padding=0:margin=0"
    assert command[-1] == str(output)


def test_failed_generation_returns_no_preview(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        sprites,
        "generate_preview_sprite",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError()),
    )

    result = try_generate_preview_sprite(tmp_path / "source.mkv", tmp_path / "previews")

    assert result is None


def test_generate_preview_sprite_writes_a_tiled_image_and_vtt(tmp_path: Path) -> None:
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

    preview = generate_preview_sprite(
        source,
        tmp_path / "previews",
        options=PreviewSpriteOptions(
            interval_seconds=0.25, tile_width=40, tile_height=22, columns=2
        ),
    )

    assert preview.image.exists()
    assert preview.vtt.exists()
    assert "sprite.jpg#xywh=40,22,40,22" in preview.vtt.read_text()
    assert _resolution(preview.image) == "80x44"


def _resolution(path: Path) -> str:
    return subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=width,height",
            "-of",
            "csv=p=0:s=x",
            str(path),
        ],
        capture_output=True,
        check=True,
        text=True,
    ).stdout.strip()
