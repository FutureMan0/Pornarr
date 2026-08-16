"""Scene-boundary detection that rides along with the preview pass.

The expensive part of finding scene cuts is not comparing frames, it is
decoding them. The sprite pass already decodes every frame of the file to
sample one every ten seconds, so detection is attached to that decode with a
`split` filter rather than run as a second pass. One decode, two outputs.

Within that shared decode the analysis branch is made deliberately cheap:

- **fps first.** Cutting the analysis to a few frames per second before any
  comparison happens is the single largest saving in the filter graph. A cut
  lasting less than a frame interval is not a cut anyone can see.
- **then scale.** Scene scoring is a whole-frame difference; at 180p it reaches
  the same conclusions as at 2160p for a fraction of the pixels.
- **audio and subtitles dropped.** Nothing here needs them decoded.
- **`-f null`.** The selected frames are counted, never encoded or written.

Ordering matters: scaling before the frame-rate cut would scale frames that are
about to be thrown away.
"""

from __future__ import annotations

import math
import re
import subprocess
from dataclasses import dataclass
from itertools import pairwise
from pathlib import Path

# Measured against ffmpeg 8.1.2 rather than guessed, because the score does not
# span 0..1 the way the name suggests. On synthetic clips through this exact
# filter graph:
#
#   hard cut between two unrelated shots   0.68
#   cut between two flat solid colours     0.40
#   motion inside one continuous shot     <0.08
#
# The usable gap is therefore between roughly 0.1 and 0.4, not around 0.5. At
# 0.3 there is margin on both sides: four times the worst in-shot motion, and
# comfortably under the weakest real cut observed.
DEFAULT_THRESHOLD = 0.3
DEFAULT_ANALYSIS_FPS = 6.0
DEFAULT_ANALYSIS_HEIGHT = 180
# Two seconds is below the shortest shot anyone navigates to deliberately, and
# it is the floor that stops a strobing sequence from emitting a marker a frame.
DEFAULT_MINIMUM_SCENE_SECONDS = 2.0
# A hard stop on pathological input. Ten thousand markers on one file is not a
# useful index, it is a denial of service against the database and the UI.
MAXIMUM_SCENES = 2000
COMMAND_TIMEOUT_SECONDS = 900

_PTS_TIME = re.compile(r"\bpts_time:(\d+(?:\.\d+)?)")


class SceneDetectionError(Exception):
    """FFmpeg could not analyse the media file."""


@dataclass(frozen=True, slots=True)
class SceneDetectionOptions:
    threshold: float = DEFAULT_THRESHOLD
    analysis_fps: float = DEFAULT_ANALYSIS_FPS
    analysis_height: int = DEFAULT_ANALYSIS_HEIGHT
    minimum_scene_seconds: float = DEFAULT_MINIMUM_SCENE_SECONDS
    maximum_scenes: int = MAXIMUM_SCENES

    def __post_init__(self) -> None:
        if not 0 < self.threshold < 1:
            raise ValueError("threshold must be between zero and one")
        if self.analysis_fps <= 0:
            raise ValueError("analysis fps must be greater than zero")
        if self.analysis_height <= 0:
            raise ValueError("analysis height must be greater than zero")
        if self.minimum_scene_seconds < 0:
            raise ValueError("minimum scene length must not be negative")
        if self.maximum_scenes <= 0:
            raise ValueError("maximum scenes must be greater than zero")


DEFAULT_OPTIONS = SceneDetectionOptions()


@dataclass(frozen=True, slots=True)
class Scene:
    """One shot, as a half-open interval in seconds from the start of the file."""

    ordinal: int
    start_seconds: float
    end_seconds: float

    @property
    def duration_seconds(self) -> float:
        return self.end_seconds - self.start_seconds


def build_scene_filter(options: SceneDetectionOptions, metadata_path: Path) -> str:
    """The analysis half of the filter graph, cheapest operation first."""
    return (
        f"fps={options.analysis_fps:g},"
        f"scale=-2:{options.analysis_height},"
        f"select='gt(scene\\,{options.threshold:g})',"
        f"metadata=print:file={_escape_filter_path(metadata_path)}"
    )


def build_scene_command(
    source: Path, metadata_path: Path, *, options: SceneDetectionOptions = DEFAULT_OPTIONS
) -> list[str]:
    """A standalone detection pass, for files that never get a preview."""
    return [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-nostdin",
        "-y",
        "-i",
        str(source),
        "-an",
        "-sn",
        "-vf",
        build_scene_filter(options, metadata_path),
        "-f",
        "null",
        "-",
    ]


def parse_cut_points(metadata: str) -> tuple[float, ...]:
    """Pull the timestamps out of one `metadata=print` dump.

    Only `pts_time` is read. The scene score itself is not retained: it is a
    detector implementation detail, and storing it would invite tuning the
    threshold against recorded numbers from a different ffmpeg build.
    """
    seen: list[float] = []
    for match in _PTS_TIME.finditer(metadata):
        value = float(match.group(1))
        if math.isfinite(value) and value >= 0:
            seen.append(value)
    return tuple(sorted(set(seen)))


def scenes_from_cuts(
    cut_points: tuple[float, ...],
    duration_seconds: float,
    *,
    options: SceneDetectionOptions = DEFAULT_OPTIONS,
) -> tuple[Scene, ...]:
    """Turn cut timestamps into contiguous scenes covering the whole file.

    A cut that would open a scene shorter than the minimum is dropped rather
    than kept as a sliver: the point of the index is somewhere to jump to, and
    a half-second marker is not somewhere anyone wants to land.
    """
    if duration_seconds <= 0:
        return ()
    boundaries = [0.0]
    for point in cut_points:
        if point <= boundaries[-1] + options.minimum_scene_seconds:
            continue
        if point >= duration_seconds - options.minimum_scene_seconds:
            break
        boundaries.append(point)
        if len(boundaries) >= options.maximum_scenes:
            break
    boundaries.append(duration_seconds)
    return tuple(
        Scene(ordinal=index, start_seconds=start, end_seconds=end)
        for index, (start, end) in enumerate(pairwise(boundaries))
    )


def detect_scenes(
    source: Path,
    duration_seconds: float,
    *,
    options: SceneDetectionOptions = DEFAULT_OPTIONS,
    metadata_directory: Path | None = None,
    timeout: float = COMMAND_TIMEOUT_SECONDS,
) -> tuple[Scene, ...]:
    """Run a standalone detection pass and return the scenes it implies."""
    directory = metadata_directory or source.parent
    metadata_path = directory / f".scenes.{source.stem}.txt"
    try:
        completed = subprocess.run(
            build_scene_command(source, metadata_path, options=options),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as error:
        metadata_path.unlink(missing_ok=True)
        raise SceneDetectionError(f"scene detection timed out after {timeout} seconds") from error
    except OSError as error:
        # A missing or unrunnable ffmpeg is still "no scenes came back". Callers
        # should not have to know that detection is a subprocess to catch it.
        metadata_path.unlink(missing_ok=True)
        raise SceneDetectionError("ffmpeg could not be run for scene detection") from error
    if completed.returncode != 0:
        metadata_path.unlink(missing_ok=True)
        raise SceneDetectionError("media file could not be analysed for scenes")
    return read_scenes(metadata_path, duration_seconds, options=options)


def read_scenes(
    metadata_path: Path,
    duration_seconds: float,
    *,
    options: SceneDetectionOptions = DEFAULT_OPTIONS,
) -> tuple[Scene, ...]:
    """Read a metadata dump written by either the fused or standalone pass.

    A missing dump means ffmpeg found no cut at all, which is a legitimate
    outcome for a single-shot file, so it yields one scene rather than an error.
    """
    try:
        metadata = metadata_path.read_text(errors="replace")
    except OSError:
        metadata = ""
    finally:
        metadata_path.unlink(missing_ok=True)
    return scenes_from_cuts(parse_cut_points(metadata), duration_seconds, options=options)


def _escape_filter_path(path: Path) -> str:
    """Quote a path for use inside a filter argument.

    Colons separate filter options and backslashes escape, so a Windows-style
    or oddly named path would otherwise be read as more options.
    """
    return str(path).replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")
