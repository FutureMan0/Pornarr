"""The scene index for a title, for chapter navigation and clip cutting.

Markers belong to a media file, but a caller has a title, so this resolves the
active file and answers for that. A title whose file was replaced and not yet
re-analysed reports an empty index rather than the previous file's cuts.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from pornarr_api.auth import database_session, get_current_user, require_role
from pornarr_api.errors import ErrorResponse
from pornarr_db.models.media import Media, MediaFile
from pornarr_db.models.scene_marker import SceneMarker
from pornarr_db.models.user import User, UserRole

router = APIRouter(prefix="/media", tags=["scenes"])
CurrentUser = Annotated[User, Depends(get_current_user)]
Admin = Annotated[User, Depends(require_role(UserRole.ADMIN))]
Session = Annotated[AsyncSession, Depends(database_session)]


class SceneResponse(BaseModel):
    id: UUID
    ordinal: int
    start_seconds: float
    end_seconds: float
    duration_seconds: float


class SceneIndexResponse(BaseModel):
    media_id: UUID
    media_file_id: UUID | None
    scenes: list[SceneResponse]


def _marker_response(marker: SceneMarker) -> SceneResponse:
    return SceneResponse(
        id=marker.id,
        ordinal=marker.ordinal,
        start_seconds=marker.start_seconds,
        end_seconds=marker.end_seconds,
        duration_seconds=round(marker.end_seconds - marker.start_seconds, 3),
    )


@router.get(
    "/{media_id}/scenes",
    response_model=SceneIndexResponse,
    responses={404: {"model": ErrorResponse}},
)
async def read_scenes(media_id: UUID, user: CurrentUser, session: Session) -> SceneIndexResponse:
    if await session.get(Media, media_id) is None:
        raise HTTPException(status_code=404)
    media_file_id = await session.scalar(
        select(MediaFile.id).where(MediaFile.media_id == media_id, MediaFile.is_active.is_(True))
    )
    if media_file_id is None:
        return SceneIndexResponse(media_id=media_id, media_file_id=None, scenes=[])
    markers = await session.scalars(
        select(SceneMarker)
        .where(SceneMarker.media_file_id == media_file_id)
        .order_by(SceneMarker.ordinal)
    )
    return SceneIndexResponse(
        media_id=media_id,
        media_file_id=media_file_id,
        scenes=[_marker_response(marker) for marker in markers],
    )


class MarkerWrite(BaseModel):
    start_seconds: Annotated[float, Field(ge=0)]
    end_seconds: Annotated[float, Field(gt=0)]

    @model_validator(mode="after")
    def ordered(self) -> MarkerWrite:
        if self.end_seconds <= self.start_seconds:
            raise ValueError("end_seconds must be after start_seconds")
        return self


class MarkerPatch(BaseModel):
    start_seconds: Annotated[float | None, Field(ge=0)] = None
    end_seconds: Annotated[float | None, Field(gt=0)] = None

    @model_validator(mode="after")
    def has_a_change(self) -> MarkerPatch:
        if self.start_seconds is None and self.end_seconds is None:
            raise ValueError("at least one field must be supplied")
        return self


async def _active_file_or_404(session: AsyncSession, media_id: UUID) -> UUID:
    if await session.get(Media, media_id) is None:
        raise HTTPException(status_code=404)
    media_file_id = await session.scalar(
        select(MediaFile.id).where(MediaFile.media_id == media_id, MediaFile.is_active.is_(True))
    )
    if media_file_id is None:
        raise HTTPException(status_code=404)
    return media_file_id


async def _marker_or_404(session: AsyncSession, media_id: UUID, marker_id: UUID) -> SceneMarker:
    media_file_id = await _active_file_or_404(session, media_id)
    marker = await session.scalar(
        select(SceneMarker).where(
            SceneMarker.id == marker_id, SceneMarker.media_file_id == media_file_id
        )
    )
    if marker is None:
        raise HTTPException(status_code=404)
    return marker


@router.post(
    "/{media_id}/markers",
    response_model=SceneResponse,
    status_code=201,
    responses={404: {"model": ErrorResponse}, 422: {"model": ErrorResponse}},
)
async def create_marker(
    media_id: UUID, payload: MarkerWrite, admin: Admin, session: Session
) -> SceneResponse:
    """Append a marker by hand, after the ones detection produced.

    Ordinals are positions in the index, not identifiers, so a manual marker
    takes the next one rather than being inserted in time order — renumbering
    every later marker would invalidate the ids clips point at.
    """
    media_file_id = await _active_file_or_404(session, media_id)
    highest = await session.scalar(
        select(func.max(SceneMarker.ordinal)).where(SceneMarker.media_file_id == media_file_id)
    )
    marker = SceneMarker(
        media_file_id=media_file_id,
        ordinal=(highest + 1) if highest is not None else 0,
        start_seconds=payload.start_seconds,
        end_seconds=payload.end_seconds,
    )
    session.add(marker)
    await session.flush()
    return _marker_response(marker)


@router.patch(
    "/{media_id}/markers/{marker_id}",
    response_model=SceneResponse,
    responses={404: {"model": ErrorResponse}, 422: {"model": ErrorResponse}},
)
async def update_marker(
    media_id: UUID, marker_id: UUID, payload: MarkerPatch, admin: Admin, session: Session
) -> SceneResponse:
    marker = await _marker_or_404(session, media_id, marker_id)
    start = payload.start_seconds if payload.start_seconds is not None else marker.start_seconds
    end = payload.end_seconds if payload.end_seconds is not None else marker.end_seconds
    if end <= start:
        raise HTTPException(status_code=422)
    marker.start_seconds, marker.end_seconds = start, end
    await session.flush()
    return _marker_response(marker)


@router.delete(
    "/{media_id}/markers/{marker_id}",
    status_code=204,
    responses={404: {"model": ErrorResponse}},
)
async def delete_marker(media_id: UUID, marker_id: UUID, admin: Admin, session: Session) -> None:
    """Clips cut from this marker keep working; their `marker_id` becomes null."""

    await session.delete(await _marker_or_404(session, media_id, marker_id))
