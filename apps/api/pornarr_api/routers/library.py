"""Paginierte, authentifizierte Bibliotheksansicht und Artwork.

Die Ansicht liest wahlweise nur die eigene Bibliothek, die eines Peers oder alle
zusammen. Beim Zusammenlesen ist die Reihenfolge das Problem: jede Instanz
sortiert selbst, und eine Seite entsteht erst aus dem k-Wege-Merge in
:mod:`pornarr_db.peers`. Ein Peer, der nicht antwortet, fehlt in
`unavailable_peers` — er bringt das Blättern nicht zum Erliegen.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from pornarr_api.auth import database_session, get_current_user
from pornarr_api.ratings_summary import (
    comment_counts,
    rating_filter,
    rating_summaries,
    tag_counts,
)
from pornarr_api.related import related_titles
from pornarr_api.routers.peers import (
    MAX_PAGE_ITEMS,
    RemoteLibraryItem,
    enabled_peers,
    fetch_peer_page,
    peer_or_404,
    proxy_path,
)
from pornarr_api.scoping import library_scope, owns
from pornarr_db.library_browse import (
    Facet,
    LibraryBrowse,
    LibraryFacets,
    LibrarySort,
    browse_conditions,
    browse_order,
    library_facets,
)
from pornarr_db.models.entities import MediaPerformer, MediaTag, Performer, Tag
from pornarr_db.models.media import Media, MediaFile
from pornarr_db.models.peer import Peer
from pornarr_db.models.playback import PlaybackProgress
from pornarr_db.models.user import User
from pornarr_db.peers import LOCAL_SOURCE, decode_cursor, encode_cursor, merge_sources
from pornarr_db.settings import get_runtime_settings

router = APIRouter(prefix="/library", tags=["library"])
media_router = APIRouter(prefix="/media", tags=["library"])
CurrentUser = Annotated[User, Depends(get_current_user)]
Session = Annotated[AsyncSession, Depends(database_session)]
ALL_SOURCES = "all"


class LibraryItemResponse(BaseModel):
    id: UUID
    title: str
    studio: str | None
    release_date: str | None
    # Wann die besitzende Instanz den Titel aufgenommen hat. Sichtbar, weil die
    # Standardsortierung sonst über Instanzgrenzen hinweg keinen Schlüssel hat:
    # ohne diesen Wert liesse sich „neueste zuerst" nur raten.
    added_at: datetime
    duration_seconds: float | None
    quality: str | None
    resolution: str | None
    position_seconds: float | None
    progress_duration_seconds: float | None
    completed: bool
    poster_url: str
    sprite_url: str | None
    rating: float | None
    rating_count: int
    tag_count: int
    comment_count: int
    # Null heisst „liegt hier". Sonst die Instanz, über deren Proxy die URLs
    # oben bereits zeigen — der Client braucht keinen Sonderfall.
    peer_id: str | None = None
    peer_name: str | None = None


class UnavailablePeerResponse(BaseModel):
    id: str
    name: str


class LibraryPageResponse(BaseModel):
    items: list[LibraryItemResponse]
    total: int
    next_offset: int | None
    next_cursor: str | None = None
    unavailable_peers: list[UnavailablePeerResponse] = Field(default_factory=list)


class FacetResponse(BaseModel):
    value: str
    count: int


class LibraryFacetsResponse(BaseModel):
    studios: list[FacetResponse]
    performers: list[FacetResponse]
    tags: list[FacetResponse]


class DetailTag(BaseModel):
    name: str
    confidence: float
    source: str


class MediaDetailResponse(BaseModel):
    id: UUID
    # Whose library this belongs to; null is the shared pool. Present on the
    # detail because the owner needs to see it, absent from search because a
    # pooled hit must not tell you whose copy it is.
    owner_id: UUID | None
    in_my_library: bool
    title: str
    studio: str | None
    release_date: str | None
    confidence: float | None
    metadata_source: str
    performers: list[str]
    tags: list[DetailTag]
    rating: float | None
    rating_count: int
    path: str
    size: int
    codecs: dict[str, object] | None
    resolution: str | None
    bitrate: int | None
    duration_seconds: float | None
    playable: bool


class TagCorrectionWrite(BaseModel):
    name: str = Field(min_length=1, max_length=256)


async def _local_page(
    request: Request,
    user: User,
    session: AsyncSession,
    *,
    browse: LibraryBrowse,
    rating_gte: float | None,
    limit: int,
    offset: int,
) -> tuple[list[LibraryItemResponse], int]:
    """One page of this instance's own library, plus how many items it has."""

    settings = await get_runtime_settings(session, request.app.state.settings)
    statement = (
        select(Media, MediaFile, PlaybackProgress)
        .join(MediaFile, (MediaFile.media_id == Media.id) & MediaFile.is_active.is_(True))
        .outerjoin(
            PlaybackProgress,
            (PlaybackProgress.media_id == Media.id) & (PlaybackProgress.user_id == user.id),
        )
    )
    scope = library_scope(user, settings)
    if scope is not None:
        statement = statement.where(scope)
    if rating_gte is not None:
        # A subquery rather than a join, so the page size still comes from the
        # outer statement and pagination stays in the database.
        statement = statement.where(Media.id.in_(rating_filter(rating_gte)))
    statement = statement.where(*browse_conditions(browse))
    rows = list(
        await session.execute(
            statement.order_by(*browse_order(browse.sort)).offset(offset).limit(limit)
        )
    )
    total = await session.scalar(
        statement.with_only_columns(func.count(func.distinct(Media.id))).order_by(None)
    )
    page_ids = [row[0].id for row in rows]
    ratings = await rating_summaries(session, page_ids)
    tags = await tag_counts(session, page_ids)
    comments = await comment_counts(session, page_ids)

    def item(
        media: Media, file: MediaFile, progress: PlaybackProgress | None
    ) -> LibraryItemResponse:
        return LibraryItemResponse(
            id=media.id,
            title=media.title,
            studio=media.studio,
            release_date=media.release_date.isoformat() if media.release_date else None,
            added_at=media.created_at,
            duration_seconds=file.duration_seconds,
            quality=file.quality,
            resolution=file.resolution,
            rating=ratings.get(media.id, (None, 0))[0],
            rating_count=ratings.get(media.id, (None, 0))[1],
            tag_count=tags.get(media.id, 0),
            comment_count=comments.get(media.id, 0),
            position_seconds=progress.position_seconds if progress else None,
            progress_duration_seconds=progress.duration_seconds if progress else None,
            completed=progress.completed if progress else False,
            poster_url=f"/api/media/{media.id}/poster",
            sprite_url=f"/api/media/{media.id}/sprite",
        )

    return [item(*row) for row in rows], total or 0


def _remote_item(peer: Peer, item: RemoteLibraryItem) -> LibraryItemResponse:
    """Turn what a peer said into an item of ours.

    Artwork and preview point at this instance's proxy, so the grid renders a
    remote title with the same markup as a local one. Playback progress is left
    empty on purpose: the peer knows the progress of the account whose key we
    hold, which is a shared service account and not the person reading this page.
    """

    return LibraryItemResponse(
        id=item.id,
        title=item.title,
        studio=item.studio,
        release_date=item.release_date.isoformat() if item.release_date else None,
        added_at=item.added_at,
        duration_seconds=item.duration_seconds,
        quality=item.quality,
        resolution=item.resolution,
        position_seconds=None,
        progress_duration_seconds=None,
        completed=False,
        poster_url=proxy_path(peer.id, f"media/{item.id}/poster"),
        sprite_url=proxy_path(peer.id, f"media/{item.id}/sprite"),
        rating=item.rating,
        rating_count=item.rating_count,
        # Ein Peer meldet diese Zahlen nicht mit; null ist hier „unbekannt",
        # nicht „keine", und das Grid blendet die Marker dann aus.
        tag_count=0,
        comment_count=0,
        peer_id=str(peer.id),
        peer_name=peer.name,
    )


def _as_utc(value: datetime) -> datetime:
    """A peer may or may not send an offset; a comparison needs one either way."""
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _merge_key(item: LibraryItemResponse, sort: LibrarySort) -> tuple[Any, ...]:
    """The value each source is already sorted by, plus the id as a tiebreak.

    The id is the same tiebreak the database uses, so a page taken from one
    source alone is in exactly the order that source would have produced.
    """

    if sort is LibrarySort.TITLE:
        return (item.title.casefold(), item.id.int)
    if sort is LibrarySort.RELEASE:
        return (item.release_date or date.min.isoformat(), item.id.int)
    if sort is LibrarySort.DURATION:
        return (item.duration_seconds if item.duration_seconds is not None else -1.0, item.id.int)
    return (_as_utc(item.added_at), item.id.int)


def _peer_params(
    browse: LibraryBrowse, rating_gte: float | None, limit: int, offset: int
) -> dict[str, str]:
    """What to ask a peer for.

    `source=local` is not a default being restated: it stops a peer from fanning
    out to *its* peers, which is what turns a ring of friends into an
    amplification loop that never ends.
    """

    params = {
        "source": LOCAL_SOURCE,
        "limit": str(limit),
        "offset": str(offset),
        "sort": browse.sort.value,
    }
    optional = {
        "studio": browse.studio,
        "performer": browse.performer,
        "tag": browse.tag,
        "quality": browse.quality,
        "rating_gte": rating_gte,
    }
    return params | {name: str(value) for name, value in optional.items() if value is not None}


@router.get("", response_model=LibraryPageResponse)
async def browse_library(
    request: Request,
    user: CurrentUser,
    session: Session,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_ITEMS)] = 48,
    offset: Annotated[int, Query(ge=0)] = 0,
    rating_gte: Annotated[float | None, Query(ge=1, le=5)] = None,
    studio: Annotated[str | None, Query(max_length=256)] = None,
    performer: Annotated[str | None, Query(max_length=256)] = None,
    tag: Annotated[str | None, Query(max_length=256)] = None,
    quality: Annotated[str | None, Query(max_length=64)] = None,
    sort: LibrarySort = LibrarySort.ADDED,
    source: Annotated[str, Query(max_length=64)] = LOCAL_SOURCE,
    cursor: Annotated[str | None, Query(max_length=1024)] = None,
) -> LibraryPageResponse:
    browse = LibraryBrowse(studio=studio, performer=performer, tag=tag, quality=quality, sort=sort)
    if source == LOCAL_SOURCE:
        items, total = await _local_page(
            request,
            user,
            session,
            browse=browse,
            rating_gte=rating_gte,
            limit=limit + 1,
            offset=offset,
        )
        return LibraryPageResponse(
            items=items[:limit],
            total=total,
            next_offset=offset + limit if len(items) > limit else None,
        )
    return await _federated_page(
        request,
        user,
        session,
        browse=browse,
        rating_gte=rating_gte,
        limit=limit,
        offset=offset,
        source=source,
        cursor=cursor,
    )


async def _sources(session: AsyncSession, source: str) -> tuple[bool, list[Peer]]:
    """Which libraries a request reads, or 404 if it named one that cannot be read."""

    if source == ALL_SOURCES:
        return True, await enabled_peers(session)
    try:
        peer = await peer_or_404(session, UUID(source))
    except ValueError as error:
        raise HTTPException(status_code=422) from error
    if not peer.enabled:
        raise HTTPException(status_code=404)
    return False, [peer]


async def _federated_page(
    request: Request,
    user: User,
    session: AsyncSession,
    *,
    browse: LibraryBrowse,
    rating_gte: float | None,
    limit: int,
    offset: int,
    source: str,
    cursor: str | None,
) -> LibraryPageResponse:
    include_local, peers = await _sources(session, source)
    try:
        offsets = decode_cursor(cursor, browse.sort.value) if cursor else {}
    except ValueError as error:
        raise HTTPException(status_code=422) from error
    if not offsets and not include_local:
        # A single peer has one position, so plain `offset` still means what it
        # means everywhere else in this API.
        offsets = {str(peers[0].id): offset}

    pages: dict[str, list[LibraryItemResponse]] = {}
    total = 0
    if include_local:
        items, local_total = await _local_page(
            request,
            user,
            session,
            browse=browse,
            rating_gte=rating_gte,
            limit=limit,
            offset=offsets.get(LOCAL_SOURCE, 0),
        )
        pages[LOCAL_SOURCE], total = items, local_total
    answers = await asyncio.gather(
        *(
            fetch_peer_page(
                request, peer, _peer_params(browse, rating_gte, limit, offsets.get(str(peer.id), 0))
            )
            for peer in peers
        )
    )
    unavailable: list[UnavailablePeerResponse] = []
    for peer, answer in zip(peers, answers, strict=True):
        if answer is None:
            unavailable.append(UnavailablePeerResponse(id=str(peer.id), name=peer.name))
            continue
        pages[str(peer.id)] = [_remote_item(peer, item) for item in answer.items]
        total += answer.total

    merged = merge_sources(
        pages,
        offsets,
        key=lambda item: _merge_key(item, browse.sort),
        descending=browse.sort is not LibrarySort.TITLE,
        limit=limit,
    )
    # A source that filled its buffer may well have more behind it, and one that
    # is only unavailable right now certainly does. Either way the cursor keeps
    # every position, including that of a peer this page never reached.
    more = (
        not merged.exhausted
        or any(len(page) >= limit for page in pages.values())
        or bool(unavailable)
    )
    resumed = offsets | merged.offsets
    # With one source there is one position, so it is also expressible as an
    # offset. Taken from the merge rather than from `offset + limit`, or a
    # client mixing the cursor and the offset would page over the same items.
    single = None if include_local else str(peers[0].id)
    return LibraryPageResponse(
        items=[item for _, item in merged.items],
        total=total,
        next_offset=resumed.get(single, offset) if single is not None and more else None,
        next_cursor=encode_cursor(browse.sort.value, resumed) if more else None,
        unavailable_peers=unavailable,
    )


@router.get("/facets", response_model=LibraryFacetsResponse)
async def library_filter_values(
    request: Request, user: CurrentUser, session: Session
) -> LibraryFacetsResponse:
    """The values worth filtering by, so the screen offers them instead of a blank box."""

    settings = await get_runtime_settings(session, request.app.state.settings)
    facets = await library_facets(session, scope=library_scope(user, settings))
    return _facets_response(facets)


def _facets_response(facets: LibraryFacets) -> LibraryFacetsResponse:
    def values(items: tuple[Facet, ...]) -> list[FacetResponse]:
        return [FacetResponse(value=item.value, count=item.count) for item in items]

    return LibraryFacetsResponse(
        studios=values(facets.studios),
        performers=values(facets.performers),
        tags=values(facets.tags),
    )


@media_router.get("/{media_id}/poster", response_class=FileResponse)
async def poster(
    media_id: UUID, request: Request, _: CurrentUser, session: Session
) -> FileResponse:
    if (
        await session.scalar(
            select(MediaFile.id).where(
                MediaFile.media_id == media_id, MediaFile.is_active.is_(True)
            )
        )
        is None
    ):
        raise HTTPException(status_code=404)
    directory = request.app.state.settings.thumbnail_path / str(media_id)
    path = directory / "poster.jpg"
    if not path.is_file():
        path = directory / "placeholder.svg"
    if not path.is_file():
        raise HTTPException(status_code=404)
    return FileResponse(path, headers={"Cache-Control": "private, max-age=3600"})


@media_router.get("/{media_id}", response_model=MediaDetailResponse)
async def media_detail(media_id: UUID, user: CurrentUser, session: Session) -> MediaDetailResponse:
    row = await session.execute(
        select(Media, MediaFile)
        .join(MediaFile, (MediaFile.media_id == Media.id) & MediaFile.is_active.is_(True))
        .where(Media.id == media_id)
    )
    result = row.one_or_none()
    if result is None:
        raise HTTPException(status_code=404)
    media, file = result
    performers = list(
        (
            await session.scalars(
                select(Performer.name)
                .join(MediaPerformer)
                .where(MediaPerformer.media_id == media_id)
            )
        ).all()
    )
    tags = list(
        (
            await session.execute(
                select(Tag.name, MediaTag.confidence, MediaTag.source)
                .join(MediaTag)
                .where(MediaTag.media_id == media_id)
            )
        ).tuples()
    )
    rating, rating_count = (await rating_summaries(session, [media.id])).get(media.id, (None, 0))
    return MediaDetailResponse(
        id=media.id,
        owner_id=media.owner_id,
        in_my_library=owns(media, user),
        title=media.title,
        studio=media.studio,
        release_date=media.release_date.isoformat() if media.release_date else None,
        confidence=media.confidence,
        metadata_source="import",
        performers=performers,
        tags=[
            DetailTag(name=name, confidence=confidence, source=source)
            for name, confidence, source in tags
        ],
        path=file.path,
        size=file.size,
        codecs=file.codecs,
        resolution=file.resolution,
        bitrate=file.bitrate,
        duration_seconds=file.duration_seconds,
        playable=not file.is_missing,
        rating=rating,
        rating_count=rating_count,
    )


@media_router.post("/{media_id}/tags", response_model=DetailTag, status_code=201)
async def correct_tag(
    media_id: UUID, payload: TagCorrectionWrite, _: CurrentUser, session: Session
) -> DetailTag:
    if await session.get(Media, media_id) is None:
        raise HTTPException(status_code=404)
    name = payload.name.strip()
    if not name:
        raise HTTPException(status_code=422)
    normalized = name.casefold()
    tag = await session.scalar(select(Tag).where(Tag.normalized_name == normalized))
    if tag is None:
        tag = Tag(name=name, normalized_name=normalized)
        session.add(tag)
        await session.flush()
    assignment = await session.scalar(
        select(MediaTag).where(
            MediaTag.media_id == media_id, MediaTag.tag_id == tag.id, MediaTag.source == "manual"
        )
    )
    if assignment is None:
        session.add(MediaTag(media_id=media_id, tag_id=tag.id, confidence=1, source="manual"))
    await session.flush()
    return DetailTag(name=tag.name, confidence=1, source="manual")


@media_router.get("/{media_id}/sprite", response_class=FileResponse)
async def sprite(
    media_id: UUID, request: Request, _: CurrentUser, session: Session
) -> FileResponse:
    """Serve the generated contact sheet only for an existing active item."""
    if (
        await session.scalar(
            select(MediaFile.id).where(
                MediaFile.media_id == media_id, MediaFile.is_active.is_(True)
            )
        )
        is None
    ):
        raise HTTPException(status_code=404)
    path = request.app.state.settings.thumbnail_path / str(media_id) / "sprite.jpg"
    if not path.is_file():
        raise HTTPException(status_code=404)
    return FileResponse(path, headers={"Cache-Control": "private, max-age=3600"})


class RelatedResponse(BaseModel):
    media_id: UUID
    title: str
    studio: str | None
    duration_seconds: float | None
    # The strongest shared signal, as a stable key. The interface is translated,
    # so the server decides *which* reason and the client decides its wording.
    reason: str
    shared_performers: int
    shared_tags: int
    rating: float | None
    rating_count: int


@media_router.get("/{media_id}/related", response_model=list[RelatedResponse])
async def media_related(
    media_id: UUID,
    request: Request,
    user: CurrentUser,
    session: Session,
    limit: Annotated[int, Query(ge=1, le=24)] = 8,
) -> list[RelatedResponse]:
    """Titles like this one.

    Scoped the same way the library is: a neighbour you are not allowed to see
    in the grid must not appear here either, or the related row becomes a way
    to enumerate someone else's private titles.
    """
    media = await session.get(Media, media_id)
    if media is None:
        raise HTTPException(status_code=404)

    settings = await get_runtime_settings(session, request.app.state.settings)
    neighbours = await related_titles(
        session, media=media, scope=library_scope(user, settings), limit=limit
    )
    ratings = await rating_summaries(session, [item.media_id for item in neighbours])
    return [
        RelatedResponse(
            media_id=item.media_id,
            title=item.title,
            studio=item.studio,
            duration_seconds=item.duration_seconds,
            reason=item.reason,
            shared_performers=item.shared_performers,
            shared_tags=item.shared_tags,
            rating=ratings.get(item.media_id, (None, 0))[0],
            rating_count=ratings.get(item.media_id, (None, 0))[1],
        )
        for item in neighbours
    ]
