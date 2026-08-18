"""Faceted browsing of the library.

Search answers "find me this"; browsing answers "show me what I have", which is
a different query: no text to match, an ordering the reader chooses, and a list
of the values worth filtering by. Keeping it here rather than folding it into
:mod:`pornarr_db.media_search` also keeps it free of the trigram operators that
only PostgreSQL has - a facet value comes from the facet list, so it is matched
exactly.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from typing import Any

from sqlalchemy import ColumnElement, Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from pornarr_db.models.entities import MediaPerformer, MediaTag, Performer, Tag
from pornarr_db.models.media import Media, MediaFile


class LibrarySort(StrEnum):
    ADDED = "added"
    TITLE = "title"
    RELEASE = "release"
    DURATION = "duration"


@dataclass(frozen=True, slots=True)
class LibraryBrowse:
    """What the library screen is currently showing."""

    studio: str | None = None
    performer: str | None = None
    tag: str | None = None
    quality: str | None = None
    sort: LibrarySort = LibrarySort.ADDED


@dataclass(frozen=True, slots=True)
class Facet:
    """One filter value, with how many items carry it."""

    value: str
    count: int


@dataclass(frozen=True, slots=True)
class LibraryFacets:
    studios: tuple[Facet, ...] = ()
    performers: tuple[Facet, ...] = ()
    tags: tuple[Facet, ...] = ()


def browse_conditions(browse: LibraryBrowse) -> tuple[ColumnElement[bool], ...]:
    """Return the filters chosen on the library screen."""

    conditions: list[ColumnElement[bool]] = []
    if browse.studio:
        conditions.append(func.lower(Media.studio) == browse.studio.casefold())
    if browse.quality:
        conditions.append(MediaFile.quality == browse.quality)
    if browse.performer:
        conditions.append(
            select(1)
            .select_from(MediaPerformer)
            .join(Performer, Performer.id == MediaPerformer.performer_id)
            .where(
                MediaPerformer.media_id == Media.id,
                Performer.normalized_name == browse.performer.casefold(),
            )
            .exists()
        )
    if browse.tag:
        conditions.append(
            select(1)
            .select_from(MediaTag)
            .join(Tag, Tag.id == MediaTag.tag_id)
            .where(
                MediaTag.media_id == Media.id,
                Tag.normalized_name == browse.tag.casefold(),
            )
            .exists()
        )
    return tuple(conditions)


def browse_order(sort: LibrarySort) -> tuple[ColumnElement[Any], ...]:
    """Return the ordering, with the media id last so paging is stable."""

    columns: dict[LibrarySort, ColumnElement[Any]] = {
        LibrarySort.ADDED: Media.created_at.desc(),
        LibrarySort.TITLE: Media.normalized_title.asc(),
        LibrarySort.RELEASE: func.coalesce(Media.release_date, date.min).desc(),
        LibrarySort.DURATION: func.coalesce(MediaFile.duration_seconds, -1).desc(),
    }
    return (
        columns[sort],
        Media.id.asc() if sort is LibrarySort.TITLE else Media.id.desc(),
    )


async def library_facets(
    session: AsyncSession, *, scope: ColumnElement[bool] | None = None, limit: int = 24
) -> LibraryFacets:
    """Return the filter values actually present, most-used first."""

    return LibraryFacets(
        studios=await _facets(session, Media.studio, scope=scope, limit=limit, joins=()),
        performers=await _facets(
            session,
            Performer.name,
            scope=scope,
            limit=limit,
            joins=(
                (MediaPerformer, MediaPerformer.media_id == Media.id),
                (Performer, Performer.id == MediaPerformer.performer_id),
            ),
        ),
        tags=await _facets(
            session,
            Tag.name,
            scope=scope,
            limit=limit,
            joins=(
                (MediaTag, MediaTag.media_id == Media.id),
                (Tag, Tag.id == MediaTag.tag_id),
            ),
        ),
    )


async def _facets(
    session: AsyncSession,
    column: Any,
    *,
    scope: ColumnElement[bool] | None,
    limit: int,
    joins: tuple[tuple[Any, ColumnElement[bool]], ...],
) -> tuple[Facet, ...]:
    statement: Select[tuple[Any, int]] = (
        select(column, func.count(func.distinct(Media.id)))
        .select_from(Media)
        .join(MediaFile, (MediaFile.media_id == Media.id) & MediaFile.is_active.is_(True))
    )
    for target, condition in joins:
        statement = statement.join(target, condition)
    if scope is not None:
        statement = statement.where(scope)
    rows = await session.execute(
        statement.where(column.is_not(None))
        .group_by(column)
        .order_by(func.count(func.distinct(Media.id)).desc(), column.asc())
        .limit(limit)
    )
    return tuple(Facet(value=value, count=count) for value, count in rows.tuples() if value)
