"""Scene detection: the cheap analysis graph, and what it makes of the cuts."""

from __future__ import annotations

import subprocess
from pathlib import Path
from shutil import which

import pytest

from pornarr_media.scenes import (
    Scene,
    SceneDetectionError,
    SceneDetectionOptions,
    build_scene_command,
    build_scene_filter,
    detect_scenes,
    parse_cut_points,
    read_scenes,
    scenes_from_cuts,
)
from pornarr_media.sprites import (
    PreviewSpriteOptions,
    build_sprite_and_scene_command,
    generate_preview_and_scenes,
)

FFMPEG_AVAILABLE = which("ffmpeg") is not None and which("ffprobe") is not None


def test_options_reject_values_that_would_make_detection_meaningless() -> None:
    with pytest.raises(ValueError, match="threshold"):
        SceneDetectionOptions(threshold=0)
    with pytest.raises(ValueError, match="threshold"):
        SceneDetectionOptions(threshold=1)
    with pytest.raises(ValueError, match="fps"):
        SceneDetectionOptions(analysis_fps=0)
    with pytest.raises(ValueError, match="height"):
        SceneDetectionOptions(analysis_height=0)
    with pytest.raises(ValueError, match="minimum scene"):
        SceneDetectionOptions(minimum_scene_seconds=-1)
    with pytest.raises(ValueError, match="maximum scenes"):
        SceneDetectionOptions(maximum_scenes=0)


def test_the_analysis_graph_thins_frames_before_it_scales_them() -> None:
    """Order is the whole optimisation: scaling frames about to be dropped is waste."""

    graph = build_scene_filter(SceneDetectionOptions(), Path("/tmp/scenes.txt"))

    assert graph.index("fps=") < graph.index("scale=") < graph.index("select=")
    assert "scale=-2:180" in graph
    assert "gt(scene\\,0.3)" in graph


def test_the_standalone_pass_decodes_no_audio_and_encodes_nothing() -> None:
    command = build_scene_command(Path("/library/a.mkv"), Path("/tmp/scenes.txt"))

    assert "-an" in command and "-sn" in command
    assert command[-3:] == ["-f", "null", "-"]


def test_the_fused_command_decodes_once_and_feeds_both_branches() -> None:
    """One input, one split, two mapped outputs — not two ffmpeg invocations."""

    command = build_sprite_and_scene_command(
        Path("/library/a.mkv"),
        Path("/out/sprite.jpg"),
        Path("/out/scenes.txt"),
        frame_count=20,
        options=PreviewSpriteOptions(),
        scene_options=SceneDetectionOptions(),
    )

    graph = command[command.index("-filter_complex") + 1]
    assert command.count("-i") == 1
    assert "[0:v]split=2[preview][analysis]" in graph
    assert "[tiles]" in graph and "[scenes]" in graph
    assert command.count("-map") == 2


def test_a_filter_path_with_a_colon_cannot_be_read_as_more_options() -> None:
    graph = build_scene_filter(SceneDetectionOptions(), Path("/tmp/od:d/scenes.txt"))

    assert "od\\:d" in graph


def test_cut_points_come_back_sorted_and_deduplicated() -> None:
    metadata = (
        "frame:12 pts:288 pts_time:12.5\nlavfi.scene_score=0.812\n"
        "frame:40 pts:960 pts_time:40\nlavfi.scene_score=0.44\n"
        "frame:12 pts:288 pts_time:12.5\nlavfi.scene_score=0.812\n"
    )

    assert parse_cut_points(metadata) == (12.5, 40.0)


def test_a_dump_with_no_cuts_yields_no_cut_points() -> None:
    assert parse_cut_points("") == ()
    assert parse_cut_points("frame:0 pts:0\nlavfi.scene_score=0.01\n") == ()


def test_cuts_become_contiguous_scenes_covering_the_whole_file() -> None:
    scenes = scenes_from_cuts((12.5, 40.0), 60.0)

    assert scenes == (
        Scene(ordinal=0, start_seconds=0.0, end_seconds=12.5),
        Scene(ordinal=1, start_seconds=12.5, end_seconds=40.0),
        Scene(ordinal=2, start_seconds=40.0, end_seconds=60.0),
    )
    assert scenes[-1].duration_seconds == 20.0


def test_a_cut_that_would_open_a_sliver_is_dropped() -> None:
    """A strobing sequence must not become a marker per flash."""

    scenes = scenes_from_cuts((10.0, 10.4, 10.8, 30.0), 60.0, options=SceneDetectionOptions())

    assert [scene.start_seconds for scene in scenes] == [0.0, 10.0, 30.0]


def test_a_cut_in_the_closing_seconds_does_not_orphan_a_tail() -> None:
    scenes = scenes_from_cuts((30.0, 59.5), 60.0)

    assert [scene.end_seconds for scene in scenes] == [30.0, 60.0]


def test_a_single_shot_file_is_one_scene_rather_than_none() -> None:
    assert scenes_from_cuts((), 42.0) == (Scene(ordinal=0, start_seconds=0.0, end_seconds=42.0),)


def test_a_file_with_no_duration_yields_no_scenes() -> None:
    assert scenes_from_cuts((10.0,), 0.0) == ()


def test_the_marker_count_is_capped_against_pathological_input() -> None:
    cuts = tuple(float(second * 3) for second in range(1, 500))

    scenes = scenes_from_cuts(cuts, 2000.0, options=SceneDetectionOptions(maximum_scenes=10))

    assert len(scenes) == 10


def test_reading_a_dump_consumes_it(tmp_path: Path) -> None:
    """The dump is scratch: leaving it behind would litter the preview directory."""

    metadata = tmp_path / "scenes.txt"
    metadata.write_text("frame:1 pts:1 pts_time:5\nlavfi.scene_score=0.9\n")

    scenes = read_scenes(metadata, 20.0)

    assert [scene.start_seconds for scene in scenes] == [0.0, 5.0]
    assert not metadata.exists()


def test_a_missing_dump_means_one_scene_not_an_error(tmp_path: Path) -> None:
    scenes = read_scenes(tmp_path / "absent.txt", 30.0)

    assert scenes == (Scene(ordinal=0, start_seconds=0.0, end_seconds=30.0),)


@pytest.mark.skipif(not FFMPEG_AVAILABLE, reason="ffmpeg and ffprobe are required")
def test_a_failed_analysis_raises_rather_than_inventing_scenes(tmp_path: Path) -> None:
    source = tmp_path / "not-a-video.mkv"
    source.write_bytes(b"nonsense")

    with pytest.raises(SceneDetectionError):
        detect_scenes(source, 10.0, metadata_directory=tmp_path)


def test_an_unrunnable_ffmpeg_surfaces_as_a_detection_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Callers catch one exception type, not the fact that this is a subprocess."""

    def missing_binary(*_args: object, **_kwargs: object) -> None:
        raise FileNotFoundError(2, "No such file or directory", "ffmpeg")

    monkeypatch.setattr("pornarr_media.scenes.subprocess.run", missing_binary)

    with pytest.raises(SceneDetectionError, match="could not be run"):
        detect_scenes(tmp_path / "a.mkv", 10.0, metadata_directory=tmp_path)


def _render_two_shot_clip(path: Path) -> None:
    """Six seconds of red then six of blue: exactly one unmistakable cut."""

    subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=c=red:s=320x180:r=12:d=6",
            "-f",
            "lavfi",
            "-i",
            "color=c=blue:s=320x180:r=12:d=6",
            "-filter_complex",
            "[0:v][1:v]concat=n=2:v=1:a=0[v]",
            "-map",
            "[v]",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(path),
        ],
        check=True,
    )


@pytest.mark.skipif(not FFMPEG_AVAILABLE, reason="ffmpeg and ffprobe are required")
def test_detection_finds_the_cut_in_a_two_shot_clip(tmp_path: Path) -> None:
    source = tmp_path / "two-shot.mp4"
    _render_two_shot_clip(source)

    scenes = detect_scenes(source, 12.0, metadata_directory=tmp_path)

    assert len(scenes) == 2
    # The cut is at six seconds; the analysis runs at 6 fps, so allow a frame.
    assert scenes[0].end_seconds == pytest.approx(6.0, abs=0.35)
    assert scenes[1].end_seconds == 12.0
    assert not list(tmp_path.glob(".scenes.*"))


@pytest.mark.skipif(not FFMPEG_AVAILABLE, reason="ffmpeg and ffprobe are required")
def test_the_fused_pass_produces_the_preview_and_the_same_cut(tmp_path: Path) -> None:
    source = tmp_path / "two-shot.mp4"
    _render_two_shot_clip(source)

    result = generate_preview_and_scenes(
        source,
        tmp_path / "previews",
        options=PreviewSpriteOptions(interval_seconds=2, tile_width=40, tile_height=22, columns=3),
    )

    assert result.preview.image.exists()
    assert result.preview.vtt.exists()
    assert len(result.scenes) == 2
    assert result.scenes[0].end_seconds == pytest.approx(6.0, abs=0.35)
    # The scratch dump does not survive into the preview directory.
    assert not list((tmp_path / "previews").glob(".scenes*"))
