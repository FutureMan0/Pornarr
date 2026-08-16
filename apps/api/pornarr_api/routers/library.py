"""Paginierte, authentifizierte Bibliotheksansicht und Artwork."""
from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from pornarr_api.auth import database_session, get_current_user
from pornarr_db.models.media import Media, MediaFile
from pornarr_db.models.playback import PlaybackProgress
from pornarr_db.models.user import User

router = APIRouter(prefix="/library", tags=["library"])
media_router = APIRouter(prefix="/media", tags=["library"])
CurrentUser = Annotated[User, Depends(get_current_user)]
Session = Annotated[AsyncSession, Depends(database_session)]


class LibraryItemResponse(BaseModel):
    id: UUID
    title: str
    studio: str | None
    release_date: str | None
    duration_seconds: float | None
    quality: str | None
    resolution: str | None
    position_seconds: float | None
    progress_duration_seconds: float | None
    completed: bool
    poster_url: str


class LibraryPageResponse(BaseModel):
    items: list[LibraryItemResponse]
    next_offset: int | None


@router.get("", response_model=LibraryPageResponse)
async def browse_library(user: CurrentUser, session: Session, limit: Annotated[int, Query(ge=1, le=100)] = 48, offset: Annotated[int, Query(ge=0)] = 0) -> LibraryPageResponse:
    rows = list(await session.execute(select(Media, MediaFile, PlaybackProgress).join(MediaFile, (MediaFile.media_id == Media.id) & MediaFile.is_active.is_(True)).outerjoin(PlaybackProgress, (PlaybackProgress.media_id == Media.id) & (PlaybackProgress.user_id == user.id)).order_by(Media.updated_at.desc(), Media.id.desc()).offset(offset).limit(limit + 1)))
    def item(media: Media, file: MediaFile, progress: PlaybackProgress | None) -> LibraryItemResponse:
        return LibraryItemResponse(id=media.id, title=media.title, studio=media.studio, release_date=media.release_date.isoformat() if media.release_date else None, duration_seconds=file.duration_seconds, quality=file.quality, resolution=file.resolution, position_seconds=progress.position_seconds if progress else None, progress_duration_seconds=progress.duration_seconds if progress else None, completed=progress.completed if progress else False, poster_url=f"/api/media/{media.id}/poster")
    return LibraryPageResponse(items=[item(*row) for row in rows[:limit]], next_offset=offset + limit if len(rows) > limit else None)


@media_router.get("/{media_id}/poster", response_class=FileResponse)
async def poster(media_id: UUID, request: Request, _: CurrentUser, session: Session) -> FileResponse:
    if await session.scalar(select(MediaFile.id).where(MediaFile.media_id == media_id, MediaFile.is_active.is_(True))) is None:
        raise HTTPException(status_code=404)
    directory = request.app.state.settings.thumbnail_path / str(media_id)
    path = directory / "poster.jpg"
    if not path.is_file(): path = directory / "placeholder.svg"
    if not path.is_file(): raise HTTPException(status_code=404)
    return FileResponse(path, headers={"Cache-Control": "private, max-age=3600"})
