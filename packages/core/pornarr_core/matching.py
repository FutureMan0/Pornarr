"""Pure scene-title parsing and explainable release matching."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from difflib import SequenceMatcher

TITLE_WEIGHT = 0.6
ATTRIBUTE_WEIGHT = 0.3
RELIABILITY_WEIGHT = 0.1

_DATE = re.compile(r"\b(20\d{2})[.\-_ ](\d{2})[.\-_ ](\d{2})\b")
# The convention a Usenet indexer actually answers with: the site, a date whose
# year is written with two digits, then the scene and its quality tokens.
# `Site.YY.MM.DD.Some.Scene.XXX.1080p.MP4-GROUP`.
#
# Deliberately dotted-only: a prefix containing " - " is the other convention
# this module already reads, where the segment before the date names performers
# rather than a site, and reading it as a site would file every scene under a
# studio called after its cast.
_SCENE = re.compile(
    r"^(?P<site>(?!.* - )[^.]+(?:[._][^.\d][^.]*)*)"
    r"[._ ](?P<year>\d{2}|20\d{2})[._-](?P<month>\d{2})[._-](?P<day>\d{2})"
    r"[._ ](?P<rest>.+)$"
)
# Where a scene title stops and its release tokens begin.
_RELEASE_TOKEN = re.compile(
    r"^(?:xxx|\d{3,4}p|mp4|mkv|avi|wmv|ts|web[-_]?dl|webrip|bluray|bdrip|dvdrip|hdtv|cam"
    r"|hevc|x26[45]|h ?26[45]|av1|xvid)$",
    re.IGNORECASE,
)
_RESOLUTION = re.compile(r"\b(2160|1080|720|480)p\b", re.IGNORECASE)
_SOURCE = re.compile(r"\b(web[ ._-]?dl|webrip|bluray|bdrip|hdtv|cam)\b", re.IGNORECASE)
_CODEC = re.compile(r"\b(hevc|x265|h[ ._-]?265|av1|x264|h[ ._-]?264|xvid)\b", re.IGNORECASE)
_GROUP = re.compile(r"(?:-|\[)([A-Za-z0-9]+)\]?$")


@dataclass(frozen=True, slots=True)
class ParsedRelease:
    title: str
    site: str | None
    resolution: str | None
    source: str | None
    codec: str | None
    group: str | None
    date: date | None
    performers: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class MatchScore:
    total: float
    breakdown: dict[str, float]


def parse_release(title: str) -> ParsedRelease:
    """Extract the stable scene tokens needed by matching and quality decisions."""
    scene = _scene(title)
    date_match = _DATE.search(title)
    release_date = scene[1] if scene is not None else _date(date_match)
    return ParsedRelease(
        # Only a title the scene pattern recognised is rewritten. The other
        # convention's title carries its studio in front, which the filename
        # metadata tier reads back out of it.
        title=title if scene is None else scene[2],
        site=None if scene is None else scene[0],
        resolution=_resolution(title),
        source=_source(title),
        codec=_codec(title),
        group=_match(_GROUP, title),
        date=release_date,
        performers=_performers(title, date_match.start() if date_match is not None else None),
    )


def _scene(title: str) -> tuple[str, date, str] | None:
    """Site, date and scene title, for a release named the way scene releases are."""

    matched = _SCENE.match(title)
    if matched is None:
        return None
    year = int(matched["year"])
    try:
        released = date(
            year + 2000 if year < 100 else year, int(matched["month"]), int(matched["day"])
        )
    except ValueError:
        return None
    words = [word for word in re.split(r"[._ ]+", matched["rest"]) if word]
    scene_title = []
    for word in words:
        if _RELEASE_TOKEN.match(word) or _GROUP.search(word):
            break
        scene_title.append(word)
    if not scene_title:
        return None
    return (
        " ".join(word for word in re.split(r"[._]+", matched["site"]) if word),
        released,
        " ".join(scene_title),
    )


def score_release(
    target: ParsedRelease, candidate: ParsedRelease, *, indexer_reliability: float
) -> MatchScore:
    """Score a candidate and return every weighted contribution for explanation."""
    breakdown = {
        "title": TITLE_WEIGHT * _title_similarity(target.title, candidate.title),
        "attributes": ATTRIBUTE_WEIGHT * _attribute_agreement(target, candidate),
        "reliability": RELIABILITY_WEIGHT * min(max(indexer_reliability, 0.0), 1.0),
    }
    return MatchScore(total=sum(breakdown.values()), breakdown=breakdown)


def _match(pattern: re.Pattern[str], title: str, *, lower: bool = False) -> str | None:
    found = pattern.search(title)
    if found is None:
        return None
    value = found.group(1)
    return value.casefold() if lower else value


def _codec(title: str) -> str | None:
    value = _match(_CODEC, title, lower=True)
    if value is None:
        return None
    normalized = re.sub(r"[ ._-]", "", value)
    return {"x265": "hevc", "h265": "hevc", "x264": "h264", "h264": "h264"}.get(
        normalized, normalized
    )


def _date(match: re.Match[str] | None) -> date | None:
    if match is None:
        return None
    try:
        return date(*(int(part) for part in match.groups()))
    except ValueError:
        return None


def _resolution(title: str) -> str | None:
    value = _match(_RESOLUTION, title)
    return f"{value}p" if value is not None else None


def _source(title: str) -> str | None:
    value = _match(_SOURCE, title, lower=True)
    if value is None:
        return None
    return {"webdl": "web-dl", "webrip": "webrip", "bluray": "bluray"}.get(
        re.sub(r"[ ._-]", "", value), re.sub(r"[ ._-]", "", value)
    )


def _performers(title: str, date_start: int | None) -> tuple[str, ...]:
    # Only a dated release names its performers in a knowable place: the
    # segment before the date. Without one, "Studio - Some Title" has a last
    # segment too, and reading it as a performer files the title itself as a
    # person - which is what every undated scene release used to get.
    if date_start is None:
        return ()
    prefix = title[:date_start]
    segments = [segment.strip() for segment in re.split(r"\s+-\s+", prefix) if segment.strip()]
    people = segments[-1] if len(segments) > 1 else ""
    return tuple(
        person.strip().title() for person in re.split(r"\s+(?:and|&)|,", people) if person.strip()
    )


def _title_similarity(left: str, right: str) -> float:
    return SequenceMatcher(None, _normalized_words(left), _normalized_words(right)).ratio()


def _normalized_words(value: str) -> str:
    return " ".join(re.findall(r"\w+", value.casefold()))


def _attribute_agreement(target: ParsedRelease, candidate: ParsedRelease) -> float:
    fields = ("resolution", "source", "codec", "date")
    expected = [field for field in fields if getattr(target, field) is not None]
    matches = sum(getattr(target, field) == getattr(candidate, field) for field in expected)
    performer_match = (
        bool(set(target.performers) & set(candidate.performers)) if target.performers else False
    )
    total = len(expected) + bool(target.performers)
    return (matches + performer_match) / total if total else 0.0
