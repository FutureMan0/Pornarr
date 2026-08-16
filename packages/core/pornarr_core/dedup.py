"""Conservative cross-indexer release grouping."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime

from pornarr_integrations.indexers import Release

_WORDS = re.compile(r"[^a-z0-9]+")


@dataclass(frozen=True, slots=True)
class IndexedRelease:
    indexer_id: str
    priority: int
    release: Release


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
            if _same_release(candidate.release, group[0].release, size_tolerance):
                group.append(candidate)
                break
        else:
            groups.append([candidate])
    return [
        ReleaseGroup(primary=ordered[0], alternates=tuple(ordered[1:]))
        for group in groups
        if (ordered := sorted(group, key=_rank))
    ]


def _rank(candidate: IndexedRelease) -> tuple[int, int, int, str]:
    release = candidate.release
    completeness = sum(
        value is not None for value in (release.download_url, release.size, release.published_at)
    )
    return (candidate.priority, -completeness, -(release.seeders or 0), release.guid)


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
    return left is None or right is None or abs((left - right).total_seconds()) <= 24 * 60 * 60
