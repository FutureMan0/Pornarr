"""Release-title normalisation for search and matching."""

from __future__ import annotations

import re

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
