"""Release-title normalisation for search, matching and display."""

from __future__ import annotations

import re
from dataclasses import dataclass

TOKEN_PATTERNS = (
    r"\b(?:2160|1080|720|576|480)p\b",
    r"\b(?:web[ .-]?dl|webrip|bluray|bdrip|dvdrip|hdtv)\b",
    r"\b(?:x264|x265|h[ .-]?264|h[ .-]?265|hevc|av1|aac|dts)\b",
)
_TOKEN_RE = re.compile("|".join(TOKEN_PATTERNS), re.IGNORECASE)
_DATE_RE = re.compile(r"(?<!\d)(19\d{2}|20\d{2})(?:[ ._/\-]\d{1,2}){0,2}(?!\d)")
_BRACKET_RE = re.compile(r"[\[\](){}]")
_RELEASE_GROUP_RE = re.compile(r"[-_.][A-Z0-9]{2,}$")
_SEPARATOR_RE = re.compile(r"[._/\\-]+")
_SPACE_RE = re.compile(r"\s+")


def normalize_title(release: str) -> str:
    """Produce a stable comparable title without discarding meaningful words."""
    unified = _unify(release)
    without_tokens = _TOKEN_RE.sub(" ", unified)
    normalized = _collapse(without_tokens)
    return normalized or unified


def _unify(value: str) -> str:
    dated = _DATE_RE.sub(lambda match: match.group(1), _RELEASE_GROUP_RE.sub("", value))
    return _collapse(_SEPARATOR_RE.sub(" ", _BRACKET_RE.sub(" ", dated)))


def _collapse(value: str) -> str:
    return _SPACE_RE.sub(" ", value).strip().casefold()


_YEAR_RE = re.compile(r"[([]?\b(19\d{2}|20\d{2})\b[)\]]?")
_FULL_DATE_RE = re.compile(r"\b(19|20)\d{2}[.\-_ ]\d{2}[.\-_ ]\d{2}\b")
# The uppercase-only rule in `_RELEASE_GROUP_RE` is for comparison, where a
# wrong strip changes a match; on a displayed title a lowercase group is just
# as much noise as an uppercase one.
_DISPLAY_GROUP_RE = re.compile(r"[-_.][A-Za-z0-9]{2,}$")
_STUDIO_SPLIT_RE = re.compile(r"\s+-\s+")
MAXIMUM_STUDIO_LENGTH = 64


@dataclass(frozen=True, slots=True)
class ReleaseName:
    """A release file name split into the parts a reader wants to see."""

    studio: str | None
    title: str


def split_release_name(release: str) -> ReleaseName:
    """Split ``Studio - Title (2026) 1080p`` into its studio and a readable title.

    Step 6 of the import pipeline. Without it the library shows the file name -
    scene tokens, release group and all - as the title of the work, and the
    studio every scene release states is thrown away.
    """

    segments = [segment.strip() for segment in _STUDIO_SPLIT_RE.split(release) if segment.strip()]
    studio: str | None = None
    if len(segments) > 1 and len(segments[0]) <= MAXIMUM_STUDIO_LENGTH:
        studio = _display(segments[0]) or None
        segments = segments[1:]
    title = _display(" - ".join(segments))
    # A name that is nothing but tokens still has to be called something, and
    # the file name is the only honest answer left.
    return ReleaseName(studio=studio, title=title or release.strip())


def _display(value: str) -> str:
    """Readable text: separators become spaces, scene tokens go, case stays."""

    # Tokens first: a release group hangs off the codec token ("x265-GRP"), so
    # stripping the group before the token leaves the token's own tail behind.
    without_tokens = _TOKEN_RE.sub(" ", value)
    without_group = _DISPLAY_GROUP_RE.sub("", without_tokens.strip())
    without_dates = _FULL_DATE_RE.sub(" ", without_group)
    spaced = _SPACE_RE.sub(" ", _SEPARATOR_RE.sub(" ", _BRACKET_RE.sub(" ", without_dates)))
    return _SPACE_RE.sub(" ", _YEAR_RE.sub(" ", spaced)).strip()
