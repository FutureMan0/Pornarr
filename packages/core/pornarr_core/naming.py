"""Release-title normalisation for search, matching and display."""

from __future__ import annotations

import re
from dataclasses import dataclass

# The token vocabulary itself, without a boundary of its own: the two readers
# below need different ones. `\b` is no boundary at all after the separator a
# release most often uses - `_` is a word character, so `\b` never fires between
# `Example_` and `2160p` - which is why the tail scan states its own.
TOKEN_PATTERNS = (
    r"(?:2160|1080|720|576|480)p",
    r"(?:web[ .-]?dl|webrip|bluray|bdrip|dvdrip|hdtv)",
    r"(?:x264|x265|h[ .-]?264|h[ .-]?265|hevc|av1|aac|dts)",
)
_TOKEN_RE = re.compile(rf"\b(?:{'|'.join(TOKEN_PATTERNS)})\b", re.IGNORECASE)
_DATE_RE = re.compile(r"(?<!\d)(19\d{2}|20\d{2})(?:[ ._/\-]\d{1,2}){0,2}(?!\d)")
_BRACKET_RE = re.compile(r"[\[\](){}]")
_SEPARATOR_RE = re.compile(r"[._/\\-]+")
_SPACE_RE = re.compile(r"\s+")
# The three patterns the tail scan below recognises, each anchored at the end of
# what is left of the name and required to start on a separator or on the string
# itself. A release puts its tokens and its group after the title, never inside
# it, and matching them anywhere was what turned `AV1 Club` into `club`,
# `AAC Blues` into `blues` and `Alice 480p Street` into `alice street`.
_TAIL_TOKEN_RE = re.compile(rf"(?:^|[\s._/\\-])(?:{'|'.join(TOKEN_PATTERNS)})\s*$", re.IGNORECASE)
# A year is kept, but scanned past: `Alice [BluRay] (2024)` puts a source name
# in front of the year, and a scan that stopped at the first thing it does not
# remove would leave it in the canonical title.
_TAIL_YEAR_RE = re.compile(r"(?:^|[\s._/\\-])((?:19|20)\d{2})\s*$")
# The release group, which is an arbitrary word and can only be recognised by
# where it sits: hanging off the end behind a hard separator, in the upper case
# scene releases write it in. Stripping it before the token pass - which is what
# `normalize_title` used to do - took the `DL` of `WEB-DL` and the `264` of
# `H.264` for a group and left `alice example web` and `alice example h` behind,
# so it is tried only once the tokens in front of it are gone. A year is not a
# group, which is why `_TAIL_YEAR_RE` is consulted first: `Москва.2024` ends in
# four digits behind a dot and is a title with a date, not a title with a group.
_TAIL_GROUP_RE = re.compile(r"[-_.][A-Z0-9]{2,}\s*$")


def normalize_title(release: str) -> str:
    """Produce a stable comparable title without discarding meaningful words."""
    unified = _unify(release)
    normalized = _flatten(_strip_release_tail(unified))
    return normalized or _flatten(unified)


def _unify(value: str) -> str:
    return _DATE_RE.sub(lambda match: match.group(1), _BRACKET_RE.sub(" ", value))


def _strip_release_tail(value: str) -> str:
    """Remove the release tokens and group from the end of a name, and only there.

    docs/pipelines/import.md:23-24 asks for release group, resolution tokens,
    codec names, separators and date variants to be stripped to a canonical
    title. All of those are positional: they follow the title in a release name.
    The canonical title is what matching compares and what the library path is
    built from, so a word removed out of the middle of a real title misfiles the
    file and poisons every later match - which is why this walks in from the end
    and stops at the first thing that is neither a token, a group nor a year,
    rather than substituting a token pattern across the whole string.
    """

    cut = len(value)
    removed: list[tuple[int, int]] = []
    while cut > 0:
        head = value[:cut]
        year = _TAIL_YEAR_RE.search(head)
        if year is not None:
            cut = year.start(1)
            continue
        match = _TAIL_TOKEN_RE.search(head) or _TAIL_GROUP_RE.search(head)
        if match is None:
            break
        removed.append((match.start(), cut))
        cut = match.start()
    stripped = value
    # Collected from the right, so every span is in front of the ones already
    # applied and no index shifts. A space rather than nothing: a span swallows
    # the whitespace behind it, and `Alice BluRay 2024 1080p` would otherwise
    # close up into `Alice2024`.
    for start, end in removed:
        stripped = f"{stripped[:start]} {stripped[end:]}"
    return stripped


def _flatten(value: str) -> str:
    return _collapse(_SEPARATOR_RE.sub(" ", value))


def _collapse(value: str) -> str:
    return _SPACE_RE.sub(" ", value).strip().casefold()


_YEAR_RE = re.compile(r"[([]?\b(19\d{2}|20\d{2})\b[)\]]?")
_FULL_DATE_RE = re.compile(r"\b(19|20)\d{2}[.\-_ ]\d{2}[.\-_ ]\d{2}\b")
# The uppercase-only rule in `_TAIL_GROUP_RE` is for comparison, where a
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
