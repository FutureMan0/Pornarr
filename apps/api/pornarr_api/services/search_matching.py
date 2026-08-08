"""Classify indexer releases against the existing media library."""

from __future__ import annotations

from dataclasses import dataclass
from difflib import SequenceMatcher
from enum import StrEnum
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from pornarr_core.matching import parse_release
from pornarr_db.models.media import Media, MediaFile
from pornarr_db.release_cache import normalize_release_title

TITLE_SIMILARITY_THRESHOLD = 0.7
MATCH_CANDIDATE_LIMIT = 10
_RESOLUTION_RANK = {"480p": 480, "720p": 720, "1080p": 1080, "2160p": 2160}


class SearchMatchKind(StrEnum):
    NEW = "new"
    PRESENT = "present"
    UPGRADE = "upgrade"


@dataclass(frozen=True, slots=True)
class SearchMatch:
    """The display classification and optional link for one external release."""

    kind: SearchMatchKind
    media_id: UUID | None
    similarity: float


async def match_release(session: AsyncSession, title: str) -> SearchMatch:
    """Return a library match for an external release without mutating the library.

    PostgreSQL uses its trigram index to bound the fuzzy candidate set. The SQLite
    fallback keeps the same outcome for isolated tests. Until v0.7 owns quality
    comparison, a higher parsed resolution than the active library file is an upgrade.
    """
    normalized_title = normalize_release_title(title)
    if not normalized_title:
        return SearchMatch(SearchMatchKind.NEW, None, 0.0)
    candidates = await _candidates(session, normalized_title)
    if not candidates:
        return SearchMatch(SearchMatchKind.NEW, None, 0.0)
    media, similarity = max(
        ((media, _similarity(normalized_title, media.normalized_title)) for media in candidates),
        key=lambda candidate: candidate[1],
    )
    if similarity < TITLE_SIMILARITY_THRESHOLD:
        return SearchMatch(SearchMatchKind.NEW, None, similarity)
    kind = (
        SearchMatchKind.UPGRADE
        if _is_resolution_upgrade(title, media.files)
        else SearchMatchKind.PRESENT
    )
    return SearchMatch(kind, media.id, similarity)


async def _candidates(session: AsyncSession, normalized_title: str) -> list[Media]:
    statement = select(Media).options(selectinload(Media.files))
    if session.get_bind().dialect.name == "postgresql":
        similarity = func.similarity(Media.normalized_title, normalized_title)
        statement = (
            statement.where(Media.normalized_title.op("%")(normalized_title))
            .order_by(similarity.desc())
            .limit(MATCH_CANDIDATE_LIMIT)
        )
    return list(await session.scalars(statement))


def _similarity(left: str, right: str) -> float:
    return SequenceMatcher(None, left, right).ratio()


def _is_resolution_upgrade(title: str, files: list[MediaFile]) -> bool:
    candidate_rank = _RESOLUTION_RANK.get(parse_release(title).resolution)
    existing_ranks = [
        _RESOLUTION_RANK[file.resolution]
        for file in files
        if file.is_active and file.resolution in _RESOLUTION_RANK
    ]
    return (
        candidate_rank is not None and bool(existing_ranks) and candidate_rank > max(existing_ranks)
    )
