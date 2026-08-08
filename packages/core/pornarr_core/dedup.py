"""Pure duplicate detection for media and indexer releases."""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from difflib import SequenceMatcher
from enum import StrEnum

_TITLE_SIMILARITY_THRESHOLD = 0.85
_MAX_DATE_DISTANCE_DAYS = 2
_MAX_DURATION_DIFFERENCE = 0.05

SIZE_TOLERANCE = 0.05
AGE_TOLERANCE_SECONDS = 2 * 24 * 60 * 60


class DuplicateClassification(StrEnum):
    DUPLICATE = "duplicate"
    UPGRADE_CANDIDATE = "upgrade_candidate"
    DISTINCT = "distinct"


class DuplicateSignal(StrEnum):
    OSHASH = "oshash"
    TITLE = "title"
    STUDIO = "studio"
    RELEASE_DATE = "release_date"
    DURATION = "duration"


@dataclass(frozen=True, slots=True)
class MediaCandidate:
    title: str
    studio: str | None
    release_date: date | None
    duration_seconds: float | None
    oshash: str | None = None


@dataclass(frozen=True, slots=True)
class DuplicateDecision:
    classification: DuplicateClassification
    signals: frozenset[DuplicateSignal]


def detect_duplicate(incoming: MediaCandidate, existing: MediaCandidate) -> DuplicateDecision:
    """Classify an exact duplicate, fuzzy upgrade candidate or distinct media."""
    if incoming.oshash and incoming.oshash == existing.oshash:
        return DuplicateDecision(
            DuplicateClassification.DUPLICATE, frozenset({DuplicateSignal.OSHASH})
        )

    signals = frozenset(
        signal
        for signal, matches in (
            (DuplicateSignal.TITLE, _title_matches(incoming.title, existing.title)),
            (DuplicateSignal.STUDIO, _studio_matches(incoming.studio, existing.studio)),
            (
                DuplicateSignal.RELEASE_DATE,
                _date_matches(incoming.release_date, existing.release_date),
            ),
            (
                DuplicateSignal.DURATION,
                _duration_matches(incoming.duration_seconds, existing.duration_seconds),
            ),
        )
        if matches
    )
    fuzzy_signals = {
        DuplicateSignal.TITLE,
        DuplicateSignal.STUDIO,
        DuplicateSignal.RELEASE_DATE,
        DuplicateSignal.DURATION,
    }
    classification = (
        DuplicateClassification.UPGRADE_CANDIDATE
        if signals == fuzzy_signals
        else DuplicateClassification.DISTINCT
    )
    return DuplicateDecision(classification, signals)


def _title_matches(incoming: str, existing: str) -> bool:
    return (
        SequenceMatcher(None, incoming.casefold(), existing.casefold()).ratio()
        > _TITLE_SIMILARITY_THRESHOLD
    )


def _studio_matches(incoming: str | None, existing: str | None) -> bool:
    return (
        bool(incoming and existing) and incoming.strip().casefold() == existing.strip().casefold()
    )


def _date_matches(incoming: date | None, existing: date | None) -> bool:
    return (
        bool(incoming and existing) and abs((incoming - existing).days) <= _MAX_DATE_DISTANCE_DAYS
    )


def _duration_matches(incoming: float | None, existing: float | None) -> bool:
    if not incoming or not existing:
        return False
    return abs(incoming - existing) / max(incoming, existing) <= _MAX_DURATION_DIFFERENCE


@dataclass(frozen=True, slots=True)
class ReleaseCandidate:
    """The comparison fields and ranking inputs for one external release."""

    key: str
    title: str
    size: int | None
    published_at: datetime | None
    info_hash: str | None
    priority: int
    healthy: bool
    completeness: int


@dataclass(frozen=True, slots=True)
class DeduplicatedRelease:
    """One displayable release with its viable fallback sources."""

    primary: ReleaseCandidate
    alternates: tuple[ReleaseCandidate, ...]


def deduplicate_releases(candidates: Iterable[ReleaseCandidate]) -> tuple[DeduplicatedRelease, ...]:
    """Group equivalent releases, retaining the healthiest highest-priority source.

    An info hash is definitive. Without comparable hashes, all of normalized title,
    size (within 5 percent), and publication age (within two days) must agree.
    """
    groups: list[list[ReleaseCandidate]] = []
    for candidate in candidates:
        group = _matching_group(candidate, groups)
        if group is None:
            groups.append([candidate])
        else:
            group.append(candidate)
    deduplicated = tuple(_rank_group(group) for group in groups)
    return tuple(sorted(deduplicated, key=lambda group: _rank(group.primary)))


def _matching_group(
    candidate: ReleaseCandidate, groups: list[list[ReleaseCandidate]]
) -> list[ReleaseCandidate] | None:
    info_hash = _normalized_hash(candidate.info_hash)
    if info_hash:
        for group in groups:
            if info_hash in _group_hashes(group):
                return group
    for group in groups:
        hashes = _group_hashes(group)
        if info_hash and hashes and info_hash not in hashes:
            continue
        if all(_matches_by_metadata(candidate, existing) for existing in group):
            return group
    return None


def _rank_group(group: list[ReleaseCandidate]) -> DeduplicatedRelease:
    ranked = tuple(sorted(group, key=_rank))
    return DeduplicatedRelease(primary=ranked[0], alternates=ranked[1:])


def _rank(candidate: ReleaseCandidate) -> tuple[bool, int, int, str]:
    return (not candidate.healthy, candidate.priority, -candidate.completeness, candidate.key)


def _group_hashes(group: list[ReleaseCandidate]) -> set[str]:
    return {
        info_hash for candidate in group if (info_hash := _normalized_hash(candidate.info_hash))
    }


def _matches_by_metadata(left: ReleaseCandidate, right: ReleaseCandidate) -> bool:
    return (
        _normalized_title(left.title) == _normalized_title(right.title)
        and bool(_normalized_title(left.title))
        and _sizes_match(left.size, right.size)
        and _ages_match(left.published_at, right.published_at)
    )


def _normalized_title(title: str) -> str:
    return " ".join(re.sub(r"[^\w]+", " ", title.casefold()).split())


def _normalized_hash(info_hash: str | None) -> str:
    return "".join(info_hash.casefold().split()) if info_hash is not None else ""


def _sizes_match(left: int | None, right: int | None) -> bool:
    if left is None or right is None or left < 0 or right < 0:
        return False
    largest = max(left, right)
    return left == right if largest == 0 else abs(left - right) / largest <= SIZE_TOLERANCE


def _ages_match(left: datetime | None, right: datetime | None) -> bool:
    if left is None or right is None:
        return False
    return abs((_as_utc(left) - _as_utc(right)).total_seconds()) <= AGE_TOLERANCE_SECONDS


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
