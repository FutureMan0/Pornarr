from __future__ import annotations

import pytest

from pornarr_core.playback import (
    DEFAULT_CLIENT_CAPABILITIES,
    ClientCapabilities,
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
