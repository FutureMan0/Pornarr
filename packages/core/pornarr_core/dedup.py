"""Conservative cross-indexer release grouping."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, date, datetime
from difflib import SequenceMatcher
from enum import StrEnum

from pornarr_integrations.indexers import Release

_WORDS = re.compile(r"[^a-z0-9]+")
_TITLE_SIMILARITY_THRESHOLD = 0.85
_MAX_DATE_DISTANCE_DAYS = 2
_MAX_DURATION_DIFFERENCE = 0.05


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
    """Preserve conservative import duplicate classification."""
    if incoming.oshash and incoming.oshash == existing.oshash:
        return DuplicateDecision(
            DuplicateClassification.DUPLICATE, frozenset({DuplicateSignal.OSHASH})
        )
    signals = frozenset(
        signal
        for signal, matches in (
            (
                DuplicateSignal.TITLE,
                SequenceMatcher(None, incoming.title.casefold(), existing.title.casefold()).ratio()
                > _TITLE_SIMILARITY_THRESHOLD,
            ),
            (
                DuplicateSignal.STUDIO,
                bool(
                    incoming.studio
                    and existing.studio
                    and incoming.studio.strip().casefold() == existing.studio.strip().casefold()
                ),
            ),
            (
                DuplicateSignal.RELEASE_DATE,
                bool(
                    incoming.release_date
                    and existing.release_date
                    and abs((incoming.release_date - existing.release_date).days)
                    <= _MAX_DATE_DISTANCE_DAYS
                ),
            ),
            (
                DuplicateSignal.DURATION,
                bool(
                    incoming.duration_seconds
                    and existing.duration_seconds
                    and abs(incoming.duration_seconds - existing.duration_seconds)
                    / max(incoming.duration_seconds, existing.duration_seconds)
                    <= _MAX_DURATION_DIFFERENCE
                ),
            ),
        )
        if matches
    )
    expected = {
        DuplicateSignal.TITLE,
        DuplicateSignal.STUDIO,
        DuplicateSignal.RELEASE_DATE,
        DuplicateSignal.DURATION,
    }
    return DuplicateDecision(
        DuplicateClassification.UPGRADE_CANDIDATE
        if signals == expected
        else DuplicateClassification.DISTINCT,
        signals,
    )


@dataclass(frozen=True, slots=True)
class IndexedRelease:
    indexer_id: str
    priority: int
    release: Release
    healthy: bool = True


@dataclass(frozen=True, slots=True)
class ReleaseGroup:
    primary: IndexedRelease
    alternates: tuple[IndexedRelease, ...]


def deduplicate(
    releases: list[IndexedRelease], *, size_tolerance: float = 0.03
) -> list[ReleaseGroup]:
    """Keep the highest-priority complete release and retain safe alternates."""
    groups: list[list[IndexedRelease]] = []
    for candidate in releases:
        for group in groups:
            if _same_group(candidate.release, group, size_tolerance):
                group.append(candidate)
                break
        else:
            groups.append([candidate])
    return [
        ReleaseGroup(primary=ordered[0], alternates=tuple(ordered[1:]))
        for group in groups
        if (ordered := sorted(group, key=_rank))
    ]


def _rank(candidate: IndexedRelease) -> tuple[bool, int, int, int, str]:
    release = candidate.release
    completeness = sum(
        value is not None for value in (release.download_url, release.size, release.published_at)
    )
    return (not candidate.healthy, candidate.priority, -completeness, -(release.seeders or 0), release.guid)


def _same_group(candidate: Release, group: list[IndexedRelease], tolerance: float) -> bool:
    hashes = {item.release.info_hash.casefold() for item in group if item.release.info_hash}
    if len(hashes) > 1:
        return False
    return all(_same_release(candidate, item.release, tolerance) for item in group)


def _same_release(left: Release, right: Release, tolerance: float) -> bool:
    if left.info_hash and right.info_hash:
        return left.info_hash.casefold() == right.info_hash.casefold()
    if _title(left.title) != _title(right.title) or left.size is None or right.size is None:
        return False
    if abs(left.size - right.size) > max(left.size, right.size) * tolerance:
        return False
    return _age_close(left.published_at, right.published_at)


def _title(value: str) -> str:
    return _WORDS.sub("", value.casefold())


def _age_close(left: datetime | None, right: datetime | None) -> bool:
    if left is None or right is None:
        return True
    left = left.replace(tzinfo=UTC) if left.tzinfo is None else left.astimezone(UTC)
    right = right.replace(tzinfo=UTC) if right.tzinfo is None else right.astimezone(UTC)
    return abs((left - right).total_seconds()) <= 24 * 60 * 60
