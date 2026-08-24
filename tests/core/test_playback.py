from __future__ import annotations

import pytest

from pornarr_core.playback import (
    DEFAULT_CLIENT_CAPABILITIES,
    ClientCapabilities,
    DirectPlayDecision,
    DirectPlayReason,
    PlaybackSource,
    decide_direct_play,
)


def test_a_modern_browser_can_direct_play_h264_aac_mp4() -> None:
    decision = decide_direct_play(
        PlaybackSource(
            container="mp4",
            video_codec="h264",
            audio_codec="aac",
            video_profile="high",
            video_level=4.1,
        ),
        DEFAULT_CLIENT_CAPABILITIES,
    )

    assert decision.direct_play is True
    assert decision.reasons == ()


@pytest.mark.parametrize(
    ("source", "reason"),
    [
        (
            PlaybackSource("mkv", "h264", "aac", "high", 4.1),
            DirectPlayReason.CONTAINER_UNSUPPORTED,
        ),
        (
            PlaybackSource("mp4", "hevc", "aac", "main", 4.1),
            DirectPlayReason.VIDEO_CODEC_UNSUPPORTED,
        ),
        (
            PlaybackSource("mp4", "h264", "dts", "high", 4.1),
            DirectPlayReason.AUDIO_CODEC_UNSUPPORTED,
        ),
        (
            PlaybackSource("mp4", "h264", "aac", "high10", 4.1),
            DirectPlayReason.VIDEO_PROFILE_UNSUPPORTED,
        ),
        (
            PlaybackSource("mp4", "h264", "aac", "high", 5.2),
            DirectPlayReason.VIDEO_LEVEL_UNSUPPORTED,
        ),
    ],
)
def test_direct_play_reports_every_specific_incompatibility(
    source: PlaybackSource, reason: DirectPlayReason
) -> None:
    decision = decide_direct_play(source, DEFAULT_CLIENT_CAPABILITIES)

    assert decision.direct_play is False
    assert decision.reasons == (reason,)


def test_the_client_capability_matrix_can_allow_a_more_capable_player() -> None:
    capabilities = ClientCapabilities(
        containers=frozenset({"mp4", "matroska"}),
        video_codecs=frozenset({"h264", "hevc"}),
        audio_codecs=frozenset({"aac", "dts"}),
        video_profiles=frozenset({"main", "high", "high10"}),
        maximum_video_level=5.2,
    )

    decision = decide_direct_play(
        PlaybackSource("matroska", "hevc", "dts", "high10", 5.1), capabilities
    )

    assert decision.direct_play is True


def test_unknown_technical_metadata_falls_back_to_transcoding() -> None:
    decision = decide_direct_play(
        PlaybackSource(None, None, None, None, None), DEFAULT_CLIENT_CAPABILITIES
    )

    assert decision.direct_play is False
    assert decision.reasons == (
        DirectPlayReason.CONTAINER_UNKNOWN,
        DirectPlayReason.VIDEO_CODEC_UNKNOWN,
        DirectPlayReason.VIDEO_PROFILE_UNKNOWN,
        DirectPlayReason.VIDEO_LEVEL_UNKNOWN,
    )


def test_a_container_family_direct_plays_when_the_client_supports_one_member() -> None:
    """ffprobe names a container after its whole demuxer family, e.g. an MP4
    comes back as "mov,mp4,m4a,3gp,3g2,mj2" rather than just "mp4"."""

    decision = decide_direct_play(
        PlaybackSource("mov,mp4,m4a,3gp,3g2,mj2", "h264", "aac", "high", 4.1),
        DEFAULT_CLIENT_CAPABILITIES,
    )

    assert decision.direct_play is True
    assert decision.reasons == ()


def test_a_tenths_level_direct_plays_against_a_decimal_maximum() -> None:
    """ffprobe reports an H.264 level as tenths in an integer: level 4.0 arrives as 40."""

    decision = decide_direct_play(
        PlaybackSource("mp4", "h264", "aac", "high", 40),
        DEFAULT_CLIENT_CAPABILITIES,
    )

    assert decision.direct_play is True
    assert decision.reasons == ()


def test_a_tenths_level_above_the_maximum_does_not_direct_play() -> None:
    decision = decide_direct_play(
        PlaybackSource("mp4", "h264", "aac", "high", 51),
        DEFAULT_CLIENT_CAPABILITIES,
    )

    assert decision.direct_play is False
    assert decision.reasons == (DirectPlayReason.VIDEO_LEVEL_UNSUPPORTED,)


def test_a_client_declaring_a_tenths_maximum_behaves_like_a_decimal_one() -> None:
    """A player may send `maximum_video_level` in either notation, so a maximum

    of 42 must accept exactly what a maximum of 4.2 accepts.
    """

    capabilities = ClientCapabilities(
        containers=frozenset({"mp4"}),
        video_codecs=frozenset({"h264"}),
        audio_codecs=frozenset({"aac"}),
        video_profiles=frozenset({"high"}),
        maximum_video_level=42,
    )

    decision = decide_direct_play(PlaybackSource("mp4", "h264", "aac", "high", 4.1), capabilities)

    assert decision.direct_play is True
    assert decision.reasons == ()


def test_constrained_baseline_is_the_baseline_profile_a_player_declares() -> None:
    """ffprobe names the most browser-compatible H.264 profile there is
    "Constrained Baseline", which is a strict subset of Baseline. Compared whole
    against a player declaring `baseline` it never matched, and every file an
    ordinary encoder produced was sent to a transcode it did not need.
    """
    source = PlaybackSource(
        container="mov,mp4,m4a,3gp,3g2,mj2",
        video_codec="h264",
        audio_codec="aac",
        video_profile="Constrained Baseline",
        video_level=22,
    )

    assert decide_direct_play(source, DEFAULT_CLIENT_CAPABILITIES) == DirectPlayDecision(
        direct_play=True, reasons=()
    )


def test_progressive_high_is_the_high_profile_a_player_declares() -> None:
    source = PlaybackSource(
        container="mp4",
        video_codec="h264",
        audio_codec="aac",
        video_profile="Progressive High",
        video_level=40,
    )

    assert decide_direct_play(source, DEFAULT_CLIENT_CAPABILITIES).direct_play is True


def test_a_high_profile_the_name_only_looks_like_is_still_refused() -> None:
    """ "High 10", "High 4:2:2" and "High 4:4:4 Predictive" all begin with the
    word High and none of them is High: each carries a bit depth or a chroma
    subsampling no browser decodes. Matching the family by prefix would answer
    direct play for a file that plays as a black screen.
    """
    for profile in ("High 10", "High 4:2:2", "High 4:4:4 Predictive"):
        source = PlaybackSource(
            container="mp4",
            video_codec="h264",
            audio_codec="aac",
            video_profile=profile,
            video_level=40,
        )

        assert decide_direct_play(source, DEFAULT_CLIENT_CAPABILITIES) == DirectPlayDecision(
            direct_play=False, reasons=(DirectPlayReason.VIDEO_PROFILE_UNSUPPORTED,)
        ), profile


def test_a_player_declaring_the_files_own_profile_name_matches_it() -> None:
    """A capable player reports the matrix it read off the file, so it declares
    ffprobe's spelling rather than the family. Normalising only the source side
    made a player that declares exactly what it is fail to match itself.
    """
    source = PlaybackSource(
        container="mp4",
        video_codec="h264",
        audio_codec="aac",
        video_profile="Constrained Baseline",
        video_level=22,
    )
    declared = ClientCapabilities(
        containers=frozenset({"mp4"}),
        video_codecs=frozenset({"h264"}),
        audio_codecs=frozenset({"aac"}),
        video_profiles=frozenset({"Constrained Baseline"}),
        maximum_video_level=22,
    )

    assert decide_direct_play(source, declared).direct_play is True
