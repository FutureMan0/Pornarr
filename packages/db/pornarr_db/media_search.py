"""PostgreSQL-backed local media search."""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from enum import StrEnum
from uuid import UUID

from sqlalchemy import Float, and_, case, func, literal, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import Select

from pornarr_db.models.entities import MediaPerformer, MediaTag, Performer, Tag
from pornarr_db.models.media import Media, MediaFile

TRIGRAM_THRESHOLD = 0.2


class MediaSort(StrEnum):
    RELEVANCE = "relevance"
    AGE = "age"
    DATE_ADDED = "date_added"
    TITLE = "title"
    SIZE = "size"
    QUALITY = "quality"
    DURATION = "duration"


@dataclass(frozen=True, slots=True)
class MediaSearch:
    query: str
    quality: str | None = None
    year: int | None = None
    studio: str | None = None
    performer: str | None = None
    tag: str | None = None
    minimum_duration_seconds: float | None = None
    maximum_duration_seconds: float | None = None
    minimum_size_bytes: int | None = None
    maximum_size_bytes: int | None = None
    maximum_age_days: int | None = None
    sort: MediaSort = MediaSort.RELEVANCE
    cursor: str | None = None
    limit: int = 50


@dataclass(frozen=True, slots=True)
class MediaSearchResult:
    media: Media
    media_file: MediaFile
    relevance: float


@dataclass(frozen=True, slots=True)
class SearchMetadata:
    performers: frozenset[str] = frozenset()
    tags: frozenset[str] = frozenset()
    has_unknown_performer_age: bool = False


def encode_cursor(values: tuple[object, UUID]) -> str:
    """Encode a stable sort value and media ID without exposing SQL details."""
    value, media_id = values
    if isinstance(value, datetime):
        value = {"datetime": value.isoformat()}
    elif isinstance(value, date):
        value = {"date": value.isoformat()}
    return base64.urlsafe_b64encode(json.dumps([value, str(media_id)]).encode()).decode()


def decode_cursor(cursor: str) -> tuple[object, UUID]:
    """Decode a cursor produced by :func:`encode_cursor`."""
    try:
        value, media_id = json.loads(base64.urlsafe_b64decode(cursor.encode()))
        if isinstance(value, dict) and set(value) == {"datetime"}:
            value = datetime.fromisoformat(value["datetime"])
        elif isinstance(value, dict) and set(value) == {"date"}:
            value = date.fromisoformat(value["date"])
        return value, UUID(media_id)
    except (TypeError, ValueError, json.JSONDecodeError) as error:
        raise ValueError("Invalid search cursor.") from error


def search_statement(search: MediaSearch) -> Select[tuple[Media, MediaFile, float]]:
    """Build an indexed PostgreSQL statement for a local media query."""
    if (
        search.minimum_size_bytes is not None
        and search.maximum_size_bytes is not None
        and search.minimum_size_bytes > search.maximum_size_bytes
    ):
        raise ValueError("minimum_size_bytes cannot exceed maximum_size_bytes.")
    query = search.query.casefold()
    title_similarity = func.similarity(Media.normalized_title, query)
    title_distance = Media.normalized_title.op("<->")(query)
    relevance = (
        (title_similarity + case((Media.normalized_title == query, 1.0), else_=0.0))
        .cast(Float)
        .label("relevance")
    )
    statement = (
        select(Media, MediaFile, relevance)
        .join(MediaFile, and_(MediaFile.media_id == Media.id, MediaFile.is_active))
        .where(Media.normalized_title.op("%")(query))
    )
    if search.quality:
        statement = statement.where(MediaFile.quality == search.quality)
    if search.year:
        statement = statement.where(
            Media.release_date >= date(search.year, 1, 1),
            Media.release_date < date(search.year + 1, 1, 1),
        )
    if search.studio:
        statement = statement.where(func.lower(Media.studio) == search.studio.casefold())
    if search.performer:
        statement = statement.where(_media_has_performer(Media.id, search.performer.casefold()))
    if search.tag:
        statement = statement.where(_media_has_tag(Media.id, search.tag.casefold()))
    if search.minimum_duration_seconds is not None:
        statement = statement.where(MediaFile.duration_seconds >= search.minimum_duration_seconds)
    if search.maximum_duration_seconds is not None:
        statement = statement.where(MediaFile.duration_seconds <= search.maximum_duration_seconds)
    if search.minimum_size_bytes is not None:
        statement = statement.where(MediaFile.size >= search.minimum_size_bytes)
    if search.maximum_size_bytes is not None:
        statement = statement.where(MediaFile.size <= search.maximum_size_bytes)
    if search.maximum_age_days is not None:
        statement = statement.where(
            Media.release_date >= datetime.now(UTC).date() - timedelta(search.maximum_age_days)
        )

    sort_column, descending = _sort_column(search.sort, relevance, title_distance)
    if search.cursor:
        value, media_id = decode_cursor(search.cursor)
        cursor_values = tuple_(literal(value), literal(media_id))
        statement = statement.where(
            tuple_(sort_column, Media.id) < cursor_values
            if descending
            else tuple_(sort_column, Media.id) > cursor_values
        )
    ordering = (
        (sort_column.desc(), Media.id.desc()) if descending else (sort_column.asc(), Media.id.asc())
    )
    return statement.order_by(*ordering).limit(min(max(search.limit, 1), 100))


async def search_media(session: AsyncSession, search: MediaSearch) -> list[MediaSearchResult]:
    """Execute a local search query and return one active file per media item."""
    await session.execute(
        select(func.set_config("pg_trgm.similarity_threshold", str(TRIGRAM_THRESHOLD), True))
    )
    rows = await session.execute(search_statement(search))
    return [
        MediaSearchResult(media=media, media_file=media_file, relevance=float(relevance))
        for media, media_file, relevance in rows.tuples()
    ]


async def search_metadata(
    session: AsyncSession, media_ids: list[UUID]
) -> dict[UUID, SearchMetadata]:
    """Fetch filter-relevant performer and tag names for a page in two queries."""
    if not media_ids:
        return {}
    performer_rows = await session.execute(
        select(MediaPerformer.media_id, Performer.name)
        .join(Performer, Performer.id == MediaPerformer.performer_id)
        .where(MediaPerformer.media_id.in_(media_ids))
    )
    tag_rows = await session.execute(
        select(MediaTag.media_id, Tag.name)
        .join(Tag, Tag.id == MediaTag.tag_id)
        .where(MediaTag.media_id.in_(media_ids))
    )
    performers: dict[UUID, set[str]] = {media_id: set() for media_id in media_ids}
    tags: dict[UUID, set[str]] = {media_id: set() for media_id in media_ids}
    for media_id, name in performer_rows.tuples():
        performers[media_id].add(name)
    for media_id, name in tag_rows.tuples():
        tags[media_id].add(name)
    return {
        media_id: SearchMetadata(
            performers=frozenset(performers[media_id]),
            tags=frozenset(tags[media_id]),
            # The current performer model has no verified-age field. Until metadata
            # import adds one, an associated performer is necessarily age-unknown.
            has_unknown_performer_age=bool(performers[media_id]),
        )
        for media_id in media_ids
    }


def _media_has_performer(media_id: object, normalized_name: str):
    return (
        select(1)
        .select_from(MediaPerformer)
        .join(Performer, Performer.id == MediaPerformer.performer_id)
        .where(
            MediaPerformer.media_id == media_id,
            Performer.normalized_name.op("%")(normalized_name),
        )
        .exists()
    )


def _media_has_tag(media_id: object, normalized_name: str):
    return (
        select(1)
        .select_from(MediaTag)
        .join(Tag, Tag.id == MediaTag.tag_id)
        .where(
            MediaTag.media_id == media_id,
            Tag.normalized_name.op("%")(normalized_name),
        )
        .exists()
    )


def _sort_column(sort: MediaSort, relevance, title_distance):
    columns = {
        MediaSort.RELEVANCE: (title_distance, False),
        MediaSort.AGE: (func.coalesce(Media.release_date, date.min), True),
        MediaSort.DATE_ADDED: (Media.created_at, True),
        MediaSort.TITLE: (Media.normalized_title, False),
        MediaSort.SIZE: (MediaFile.size, True),
        MediaSort.QUALITY: (MediaFile.quality, True),
        MediaSort.DURATION: (func.coalesce(MediaFile.duration_seconds, -1), True),
    }
    return columns[sort]
