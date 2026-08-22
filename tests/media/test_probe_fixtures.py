"""Direct-play decisions driven by captured `ffprobe -of json` output.

The fixtures under `fixtures/probing/` are the verbatim stdout of
`ffprobe -v error -show_format -show_streams -of json` for five real encodes
produced with the FFmpeg the project ships with. Nothing here runs ffprobe: the
subject is the chain that turns its output into a playback decision, which is
where the product decides whether a viewer gets the file or the GPU.

That chain is `probe()` -> `codec_payload()` (what the import writes into
`MediaFile.codecs`) -> `_source()` (what `/api/media/{id}/playback-info` reads)
-> `decide_direct_play()`. Testing it end to end from captured tool output is
the only way to catch a mismatch between the shape ffprobe emits and the shape
the decision expects, because every layer in between is happy with either.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from pornarr_api.routers.playback import _source
from pornarr_core.playback import (
    DEFAULT_CLIENT_CAPABILITIES,
    ClientCapabilities,
    DirectPlayReason,
    decide_direct_play,
)
from pornarr_media.probe import ProbeResult, codec_payload, probe

FIXTURES = Path(__file__).parent / "fixtures" / "probing"


def probed(name: str, monkeypatch: pytest.MonkeyPatch) -> ProbeResult:
    """Run the real parser over one captured ffprobe document."""

    payload = (FIXTURES / f"{name}.json").read_text()
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args, 0, payload, ""),
    )
    return probe(Path("/data/library/captured.mp4"))


def decision(name: str, capabilities: ClientCapabilities, monkeypatch: pytest.MonkeyPatch):
    return decide_direct_play(_source(codec_payload(probed(name, monkeypatch))), capabilities)


@pytest.mark.parametrize(
    ("name", "container", "video_codec", "profile", "level", "audio_codec"),
    [
        ("h264-high-aac-mp4", "mov,mp4,m4a,3gp,3g2,mj2", "h264", "High", 40, "aac"),
        (
            "h264-baseline-aac-mp4",
            "mov,mp4,m4a,3gp,3g2,mj2",
            "h264",
            "Constrained Baseline",
            30,
            "aac",
        ),
        ("hevc-aac-mp4", "mov,mp4,m4a,3gp,3g2,mj2", "hevc", "Main", 60, "aac"),
        ("vp9-opus-webm", "matroska,webm", "vp9", "Profile 0", -99, "opus"),
        ("h264-ac3-mkv", "matroska,webm", "h264", "High", 40, "ac3"),
    ],
)
def test_the_import_records_exactly_what_ffprobe_reported(
    monkeypatch: pytest.MonkeyPatch,
    name: str,
    container: str,
    video_codec: str,
    profile: str,
    level: int,
    audio_codec: str,
) -> None:
    """Pin the shape, not a paraphrase of it.

    FFmpeg reports a container as the whole family of formats its demuxer
    handles, a profile as a display name, and an H.264 level as tenths in an
    integer. Every one of those is a trap for a comparison written against the
    names a human would have used, so they are asserted verbatim.
    """

    payload = codec_payload(probed(name, monkeypatch))

    assert payload == {
        "container": container,
        "video": {"codec": video_codec, "profile": profile, "level": level},
        "audio": {"codec": audio_codec},
    }


# The remaining captured sources against the baseline every browser satisfies:
# MP4, H.264, AAC, a mainstream profile, level 4.2 or below. Each of these
# genuinely needs transcoding. The two H.264 AAC MP4 captures are the exact
# combination the baseline was written for and direct-play; they are covered by
# `test_a_browser_standard_mp4_is_direct_played` and by the Constrained Baseline
# case in `tests/core/test_playback.py`.
@pytest.mark.parametrize(
    ("name", "expected_reasons"),
    [
        (
            # No profile reason: HEVC's "Main" collides with H.264's "main" in
            # a profile list that is not qualified by codec, so an HEVC file is
            # judged against H.264's profile names and happens to pass.
            "hevc-aac-mp4",
            (
                DirectPlayReason.VIDEO_CODEC_UNSUPPORTED,
                DirectPlayReason.VIDEO_LEVEL_UNSUPPORTED,
            ),
        ),
        (
            "vp9-opus-webm",
            (
                DirectPlayReason.CONTAINER_UNSUPPORTED,
                DirectPlayReason.VIDEO_CODEC_UNSUPPORTED,
                DirectPlayReason.AUDIO_CODEC_UNSUPPORTED,
                DirectPlayReason.VIDEO_PROFILE_UNSUPPORTED,
            ),
        ),
        (
            "h264-ac3-mkv",
            (
                DirectPlayReason.CONTAINER_UNSUPPORTED,
                DirectPlayReason.AUDIO_CODEC_UNSUPPORTED,
            ),
        ),
    ],
)
def test_every_captured_source_against_the_default_browser_baseline(
    monkeypatch: pytest.MonkeyPatch, name: str, expected_reasons: tuple[DirectPlayReason, ...]
) -> None:
    result = decision(name, DEFAULT_CLIENT_CAPABILITIES, monkeypatch)

    assert result.reasons == expected_reasons
    assert result.direct_play is False


def test_a_player_that_declares_what_ffprobe_reported_direct_plays(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The comparison itself is sound; only the vocabulary on one side is not.

    Declaring the container family, the display profile and the level in tenths
    makes every field match, which localises the defect above to the values
    `DEFAULT_CLIENT_CAPABILITIES` is written in rather than to
    `decide_direct_play`.
    """

    capable = ClientCapabilities(
        containers=frozenset({"mov,mp4,m4a,3gp,3g2,mj2"}),
        video_codecs=frozenset({"h264"}),
        audio_codecs=frozenset({"aac"}),
        video_profiles=frozenset({"high"}),
        maximum_video_level=42,
    )

    result = decision("h264-high-aac-mp4", capable, monkeypatch)

    assert (result.direct_play, result.reasons) == (True, ())


def test_a_browser_standard_mp4_is_direct_played(monkeypatch: pytest.MonkeyPatch) -> None:
    """docs/pipelines/transcode.md L5-8: a file whose container, codecs, profile

    and level the client can play is streamed directly. An H.264 High
    level-4.0 AAC MP4 is that file for every browser, so it must not be turned
    away for arriving in ffprobe's own vocabulary: the container as its whole
    demuxer family and the level as tenths in an integer.
    """
    result = decision("h264-high-aac-mp4", DEFAULT_CLIENT_CAPABILITIES, monkeypatch)

    assert (result.direct_play, result.reasons) == (True, ())


def test_a_scanned_file_carries_no_technical_metadata_to_decide_on() -> None:
    """What a library scan writes before its queued probe has run: nothing.

    `apps/worker/pornarr_worker/jobs/scan.py:72-81` writes a `MediaFile` with a
    path, a size and an mtime, and leaves probing to a job it queues
    separately so a large library is not walked behind however slow ffprobe
    is. Until that job runs, `codecs` is null and the decision has no choice
    but to report four unknowns.
    """

    result = decide_direct_play(_source(None), DEFAULT_CLIENT_CAPABILITIES)

    assert result.reasons == (
        DirectPlayReason.CONTAINER_UNKNOWN,
        DirectPlayReason.VIDEO_CODEC_UNKNOWN,
        DirectPlayReason.VIDEO_PROFILE_UNKNOWN,
        DirectPlayReason.VIDEO_LEVEL_UNKNOWN,
    )
    assert result.direct_play is False


def test_a_corrupt_probe_document_is_an_error_rather_than_an_empty_decision(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A truncated document must not read as "this file has no video stream"."""

    truncated = json.dumps({"streams": [{"codec_type": "video", "codec_name": "h264"}]})
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args, 0, truncated, ""),
    )

    with pytest.raises(Exception, match="could not be probed"):
        probe(Path("/data/library/truncated.mp4"))
