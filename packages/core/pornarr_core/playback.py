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
    elif not _container_supported(source.container, capabilities.containers):
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
    elif not _supported(
        _profile_family(source.video_profile),
        # Both sides, for the same reason the level is normalised on both: a
        # capable player may declare the matrix it read off the file itself, so
        # "Constrained Baseline" has to match "Constrained Baseline" as surely
        # as it matches "baseline".
        frozenset(_profile_family(profile) for profile in capabilities.video_profiles),
    ):
        reasons.append(DirectPlayReason.VIDEO_PROFILE_UNSUPPORTED)

    if source.video_level is None:
        reasons.append(DirectPlayReason.VIDEO_LEVEL_UNKNOWN)
    elif _normalized_level(source.video_level) > _normalized_level(
        capabilities.maximum_video_level
    ):
        reasons.append(DirectPlayReason.VIDEO_LEVEL_UNSUPPORTED)

    return DirectPlayDecision(direct_play=not reasons, reasons=tuple(reasons))


def _supported(value: str, supported: frozenset[str]) -> bool:
    return value.casefold() in {candidate.casefold() for candidate in supported}


def _container_supported(container: str, supported: frozenset[str]) -> bool:
    """ffprobe names a container after its whole demuxer family rather than one
    format, so an MP4 arrives as "mov,mp4,m4a,3gp,3g2,mj2" and a whole-string
    comparison against a client that declares "mp4" never matches. A client
    able to play any single member of that family can play the file, so the
    match has to consider the family's individual names as well as the string
    ffprobe actually reported.
    """
    names = {container.casefold(), *(name.casefold() for name in container.split(","))}
    return not names.isdisjoint(candidate.casefold() for candidate in supported)


# The two H.264 profiles ffprobe names differently from the family a player
# declares, and that really are that family. Constrained Baseline is a strict
# subset of Baseline and Progressive High a strict subset of High, so a decoder
# that accepts the family accepts these.
#
# Deliberately only these two. "High 10", "High 4:2:2" and "High 4:4:4
# Predictive" all begin with "High" and none of them is High: they carry a bit
# depth or a chroma subsampling no browser decodes, and mapping them onto the
# family by prefix would answer direct-play for a file that plays as a black
# screen. Anything not named here is compared as itself and stays refused.
_PROFILE_FAMILIES = {
    "constrained baseline": "baseline",
    "progressive high": "high",
}


def _profile_family(profile: str) -> str:
    """ffprobe's descriptive profile name, as the family a client declares.

    A player reports the matrix it supports in short names — `baseline`, `main`,
    `high` — while ffprobe reports what the encoder wrote, and the most
    browser-compatible H.264 profile there is arrives as "Constrained Baseline".
    Compared whole against `{"baseline", "main", "high"}` it never matched, so
    every such file was sent to a transcode it did not need.
    """
    return _PROFILE_FAMILIES.get(profile.casefold(), profile)


def _normalized_level(level: float) -> float:
    """H.264 levels run 1.0-6.2, but ffprobe reports one as tenths in an integer
    (level 4.0 arrives as 40), while a human, and a player's query parameter,
    writes the decimal form. A value of 10 or higher can only be the tenths
    form, so that threshold converts either notation to the same unit without
    needing to be told which one it was given.
    """
    return level / 10 if level >= 10 else level
