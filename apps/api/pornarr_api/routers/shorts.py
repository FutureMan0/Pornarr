"""The Shorts feed — vertical clips under a minute, cut from the library.

A short is an offset pair into an existing title, not a rendered file. The
player already supports ranged playback, so a clip costs one row and no disk,
and deleting a short never touches media.

Creating them is an administrator's job: a short points at library content, and
letting every guest mint clips would make the feed unmoderatable.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from pornarr_api.auth import database_session, get_current_user, require_role
from pornarr_api.errors import ErrorResponse
from pornarr_api.routers.ratings import media_or_404
from pornarr_db.models.media import Media, MediaFile
from pornarr_db.models.playback import UserEvent, UserEventType
from pornarr_db.models.scene_marker import SceneMarker
from pornarr_db.models.social import (
    DEFAULT_SHORT_SECONDS,
    MAXIMUM_SHORT_SECONDS,
    Comment,
    Rating,
    Short,
    ShortSource,
)
from pornarr_db.models.user import User, UserRole
from pornarr_shared.errors import PornarrError

router = APIRouter(prefix="/shorts", tags=["shorts"])
admin_router = APIRouter(prefix="/admin/shorts", tags=["admin"])
CurrentUser = Annotated[User, Depends(get_current_user)]
Admin = Annotated[User, Depends(require_role(UserRole.ADMIN))]
Session = Annotated[AsyncSession, Depends(database_session)]

MAXIMUM_PAGE_SIZE = 100
# What "trending" looks back over.
TRENDING_WINDOW = timedelta(days=7)


class ShortSort(StrEnum):
    TRENDING = "trending"
    NEWEST = "newest"
    TOP = "top"
    DURATION = "duration"


class ShortOverlapError(PornarrError):
    code = "SHORT_ALREADY_EXISTS"
    status = 409


class ShortsWithoutMarkersError(PornarrError):
    code = "SHORTS_NO_MARKERS"
    status = 409


class ShortCreate(BaseModel):
    media_id: UUID
    title: Annotated[str, Field(min_length=1, max_length=512)]
    start_seconds: Annotated[float, Field(ge=0)]
    end_seconds: Annotated[float, Field(gt=0)]
    source: ShortSource = ShortSource.MANUAL

    @field_validator("title")
    @classmethod
    def strip_title(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("must not be blank")
        return stripped

    @model_validator(mode="after")
    def within_clip_length(self) -> ShortCreate:
        if self.end_seconds <= self.start_seconds:
            raise ValueError("end_seconds must be after start_seconds")
        if self.end_seconds - self.start_seconds > MAXIMUM_SHORT_SECONDS:
            raise ValueError(f"a short must be at most {MAXIMUM_SHORT_SECONDS:g} seconds")
        return self


class ShortResponse(BaseModel):
    id: UUID
    media_id: UUID
    # The same value as `media_id`, named for what the player needs it for:
    # the "Full title" control jumps from a clip back to what it was cut from.
    parent_media_id: UUID
    marker_id: UUID | None
    media_title: str
    title: str
    start_seconds: float
    end_seconds: float
    duration_seconds: float
    source: ShortSource
    average_stars: float | None
    comment_count: int


async def _shorts_response(
    session: AsyncSession, rows: list[tuple[Short, str]]
) -> list[ShortResponse]:
    """Aggregate the rail counters for the whole page in two queries, not per clip."""

    if not rows:
        return []
    media_ids = {short.media_id for short, _ in rows}
    averages = {
        media_id: float(average)
        for media_id, average in await session.execute(
            select(Rating.media_id, func.avg(Rating.stars))
            .where(Rating.media_id.in_(media_ids))
            .group_by(Rating.media_id)
        )
    }
    comments = dict(
        (
            await session.execute(
                select(Comment.media_id, func.count())
                .where(Comment.media_id.in_(media_ids))
                .group_by(Comment.media_id)
            )
        )
        .tuples()
        .all()
    )
    return [
        ShortResponse(
            id=short.id,
            media_id=short.media_id,
            parent_media_id=short.media_id,
            marker_id=short.marker_id,
            media_title=media_title,
            title=short.title,
            start_seconds=short.start_seconds,
            end_seconds=short.end_seconds,
            duration_seconds=round(short.end_seconds - short.start_seconds, 3),
            source=short.source,
            average_stars=round(averages[short.media_id], 2)
            if short.media_id in averages
            else None,
            comment_count=comments.get(short.media_id, 0),
        )
        for short, media_title in rows
    ]


@router.get("", response_model=list[ShortResponse])
async def list_shorts(
    user: CurrentUser,
    session: Session,
    sort: ShortSort = ShortSort.TRENDING,
    limit: Annotated[int, Field(ge=1, le=MAXIMUM_PAGE_SIZE)] = 20,
    offset: Annotated[int, Field(ge=0)] = 0,
) -> list[ShortResponse]:
    """Trending is recent watching, not all-time rating.

    A clip cut yesterday that everyone opened should lead over one from March
    with a slightly better average, so trending counts progress events on the
    parent title within the window rather than sorting by score.
    """
    statement = select(Short, Media.title).join(Media, Media.id == Short.media_id)
    if sort is ShortSort.NEWEST:
        statement = statement.order_by(Short.created_at.desc(), Short.id)
    elif sort is ShortSort.DURATION:
        statement = statement.order_by((Short.end_seconds - Short.start_seconds).asc(), Short.id)
    elif sort is ShortSort.TOP:
        average = (
            select(Rating.media_id, func.avg(Rating.stars).label("stars"))
            .group_by(Rating.media_id)
            .subquery()
        )
        statement = statement.outerjoin(average, average.c.media_id == Short.media_id).order_by(
            func.coalesce(average.c.stars, 0).desc(), Short.created_at.desc(), Short.id
        )
    else:
        since = datetime.now(UTC) - TRENDING_WINDOW
        recent = (
            select(UserEvent.media_id, func.count().label("plays"))
            .where(
                UserEvent.event_type == UserEventType.PROGRESS.value,
                UserEvent.created_at >= since,
            )
            .group_by(UserEvent.media_id)
            .subquery()
        )
        statement = statement.outerjoin(recent, recent.c.media_id == Short.media_id).order_by(
            func.coalesce(recent.c.plays, 0).desc(), Short.created_at.desc(), Short.id
        )
    rows = list(await session.execute(statement.limit(limit).offset(offset)))
    return await _shorts_response(session, [(short, title) for short, title in rows])


@router.get("/{short_id}", response_model=ShortResponse, responses={404: {"model": ErrorResponse}})
async def read_short(short_id: UUID, user: CurrentUser, session: Session) -> ShortResponse:
    row = (
        await session.execute(
            select(Short, Media.title)
            .join(Media, Media.id == Short.media_id)
            .where(Short.id == short_id)
        )
    ).first()
    if row is None:
        raise HTTPException(status_code=404)
    return (await _shorts_response(session, [(row[0], row[1])]))[0]


@admin_router.post(
    "",
    response_model=ShortResponse,
    status_code=201,
    responses={
        404: {"model": ErrorResponse},
        409: {"model": ErrorResponse},
        422: {"model": ErrorResponse},
    },
)
async def create_short(payload: ShortCreate, admin: Admin, session: Session) -> ShortResponse:
    media = await media_or_404(session, payload.media_id)
    short = Short(
        media_id=payload.media_id,
        title=payload.title,
        start_seconds=payload.start_seconds,
        end_seconds=payload.end_seconds,
        source=payload.source,
    )
    session.add(short)
    try:
        await session.flush()
    except IntegrityError as error:
        await session.rollback()
        raise ShortOverlapError("A short already starts at that offset.") from error
    return (await _shorts_response(session, [(short, media.title)]))[0]


@admin_router.delete("/{short_id}", status_code=204, responses={404: {"model": ErrorResponse}})
async def delete_short(short_id: UUID, admin: Admin, session: Session) -> None:
    short = await session.get(Short, short_id)
    if short is None:
        raise HTTPException(status_code=404)
    await session.delete(short)


class GenerateShortsWrite(BaseModel):
    """How many clips to cut, and how long each should be."""

    maximum: Annotated[int, Field(ge=1, le=20)] = 5
    seconds: Annotated[float, Field(gt=0, le=MAXIMUM_SHORT_SECONDS)] = DEFAULT_SHORT_SECONDS


@router.post(
    "/media/{media_id}/generate",
    response_model=list[ShortResponse],
    status_code=201,
    responses={404: {"model": ErrorResponse}, 409: {"model": ErrorResponse}},
)
async def generate_from_markers(
    media_id: UUID, payload: GenerateShortsWrite, admin: Admin, session: Session
) -> list[ShortResponse]:
    """Cut clips at the detected scene boundaries of a title.

    One clip per marker, in order, skipping any that would overlap a clip that
    already exists — so this never disturbs a cut placed by hand, and never
    re-cuts a marker it has already covered.

    `maximum` bounds one call rather than the title: calling again picks up at
    the first uncovered marker, so a long film can be worked through a few
    clips at a time instead of producing a hundred in one press.

    A marker is a boundary, not a length: the clip runs from the boundary for
    the requested duration or to the end of the scene, whichever is shorter, so
    a five-second shot does not become a minute of the following one.
    """
    media = await media_or_404(session, media_id)
    media_file = await session.scalar(
        select(MediaFile).where(MediaFile.media_id == media_id, MediaFile.is_active.is_(True))
    )
    if media_file is None:
        raise HTTPException(status_code=404)
    markers = list(
        await session.scalars(
            select(SceneMarker)
            .where(SceneMarker.media_file_id == media_file.id)
            .order_by(SceneMarker.ordinal)
        )
    )
    if not markers:
        raise ShortsWithoutMarkersError("This title has no scene markers to cut from.")
    taken = [
        (start, end)
        for start, end in (
            await session.execute(
                select(Short.start_seconds, Short.end_seconds).where(Short.media_id == media_id)
            )
        ).tuples()
    ]
    created: list[Short] = []
    for marker in markers:
        if len(created) >= payload.maximum:
            break
        start = marker.start_seconds
        end = min(marker.end_seconds, start + payload.seconds)
        if end <= start or any(start < other_end and other < end for other, other_end in taken):
            continue
        short = Short(
            media_id=media_id,
            marker_id=marker.id,
            title=f"{media.title} — {int(start) // 60:d}:{int(start) % 60:02d}"[:512],
            start_seconds=start,
            end_seconds=end,
            source=ShortSource.MARKER,
        )
        session.add(short)
        taken.append((start, end))
        created.append(short)
    await session.flush()
    return await _shorts_response(session, [(short, media.title) for short in created])
