"""Pure direct-play compatibility decisions."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class DirectPlayReason(StrEnum):
    CONTAINER_UNKNOWN = "container_unknown"
    CONTAINER_UNSUPPORTED = "container_unsupported"
    VIDEO_CODEC_UNKNOWN = "video_codec_unknown"
    VIDEO_CODEC_UNSUPPORTED = "video_codec_unsupported"
    AUDIO_CODEC_UNSUPPORTED = "audio_codec_unsupported"
    VIDEO_PROFILE_UNKNOWN = "video_profile_unknown"
    VIDEO_PROFILE_UNSUPPORTED = "video_profile_unsupported"
    VIDEO_LEVEL_UNKNOWN = "video_level_unknown"
    VIDEO_LEVEL_UNSUPPORTED = "video_level_unsupported"


@dataclass(frozen=True, slots=True)
class PlaybackSource:
    container: str | None
    video_codec: str | None
    audio_codec: str | None
    video_profile: str | None
    video_level: float | None


@dataclass(frozen=True, slots=True)
class ClientCapabilities:
    containers: frozenset[str]
    video_codecs: frozenset[str]
    audio_codecs: frozenset[str]
    video_profiles: frozenset[str]
    maximum_video_level: float


@dataclass(frozen=True, slots=True)
class DirectPlayDecision:
    direct_play: bool
    reasons: tuple[DirectPlayReason, ...]


# A missing player report must not assume codec support that the browser may not
# have. This baseline covers the MP4/H.264/AAC combination every modern browser
# handles, while capable players can supply their broader matrix.
DEFAULT_CLIENT_CAPABILITIES = ClientCapabilities(
    containers=frozenset({"mp4"}),
    video_codecs=frozenset({"h264"}),
    audio_codecs=frozenset({"aac"}),
    video_profiles=frozenset({"baseline", "main", "high"}),
    maximum_video_level=4.2,
)


def decide_direct_play(
    source: PlaybackSource, capabilities: ClientCapabilities
) -> DirectPlayDecision:
    """Return the complete, stable list of client incompatibilities."""
    reasons: list[DirectPlayReason] = []
    if source.container is None:
        reasons.append(DirectPlayReason.CONTAINER_UNKNOWN)
    elif not _supported(source.container, capabilities.containers):
        reasons.append(DirectPlayReason.CONTAINER_UNSUPPORTED)

    if source.video_codec is None:
        reasons.append(DirectPlayReason.VIDEO_CODEC_UNKNOWN)
    elif not _supported(source.video_codec, capabilities.video_codecs):
        reasons.append(DirectPlayReason.VIDEO_CODEC_UNSUPPORTED)

    if source.audio_codec is not None and not _supported(
        source.audio_codec, capabilities.audio_codecs
    ):
        reasons.append(DirectPlayReason.AUDIO_CODEC_UNSUPPORTED)

    if source.video_profile is None:
        reasons.append(DirectPlayReason.VIDEO_PROFILE_UNKNOWN)
    elif not _supported(source.video_profile, capabilities.video_profiles):
        reasons.append(DirectPlayReason.VIDEO_PROFILE_UNSUPPORTED)

    if source.video_level is None:
        reasons.append(DirectPlayReason.VIDEO_LEVEL_UNKNOWN)
    elif source.video_level > capabilities.maximum_video_level:
        reasons.append(DirectPlayReason.VIDEO_LEVEL_UNSUPPORTED)

    return DirectPlayDecision(direct_play=not reasons, reasons=tuple(reasons))


def _supported(value: str, supported: frozenset[str]) -> bool:
    return value.casefold() in {candidate.casefold() for candidate in supported}
