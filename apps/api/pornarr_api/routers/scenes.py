"""The scene index for a title, for chapter navigation and clip cutting.

Markers belong to a media file, but a caller has a title, so this resolves the
active file and answers for that. A title whose file was replaced and not yet
re-analysed reports an empty index rather than the previous file's cuts.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from pornarr_api.auth import database_session, get_current_user
from pornarr_api.errors import ErrorResponse
from pornarr_db.models.media import Media, MediaFile
from pornarr_db.models.scene_marker import SceneMarker
from pornarr_db.models.user import User

router = APIRouter(prefix="/media", tags=["scenes"])
CurrentUser = Annotated[User, Depends(get_current_user)]
Session = Annotated[AsyncSession, Depends(database_session)]


class SceneResponse(BaseModel):
    ordinal: int
    start_seconds: float
    end_seconds: float
    duration_seconds: float


class SceneIndexResponse(BaseModel):
    media_id: UUID
    media_file_id: UUID | None
    scenes: list[SceneResponse]


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
        scenes=[
            SceneResponse(
                ordinal=marker.ordinal,
                start_seconds=marker.start_seconds,
                end_seconds=marker.end_seconds,
                duration_seconds=round(marker.end_seconds - marker.start_seconds, 3),
            )
            for marker in markers
        ],
    )
