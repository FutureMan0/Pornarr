"""Paginierte, authentifizierte Bibliotheksansicht und Artwork."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from pornarr_api.auth import database_session, get_current_user
from pornarr_db.models.entities import MediaPerformer, MediaTag, Performer, Tag
from pornarr_db.models.media import Media, MediaFile
from pornarr_db.models.playback import PlaybackProgress
from pornarr_db.models.user import User

router = APIRouter(prefix="/library", tags=["library"])
home_router = APIRouter(prefix="/home", tags=["library"])
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
    sprite_url: str | None


class LibraryPageResponse(BaseModel):
    items: list[LibraryItemResponse]
    next_offset: int | None


class HomeResponse(BaseModel):
    continue_watching: list[LibraryItemResponse]
    recently_added: list[LibraryItemResponse]


class DetailTag(BaseModel):
    name: str
    confidence: float
    source: str


class MediaDetailResponse(BaseModel):
    id: UUID
    title: str
    studio: str | None
    release_date: str | None
    confidence: float | None
    metadata_source: str
    performers: list[str]
    tags: list[DetailTag]
    path: str
    size: int
    codecs: dict[str, object] | None
    resolution: str | None
    bitrate: int | None
    duration_seconds: float | None
    playable: bool


class TagCorrectionWrite(BaseModel):
    name: str = Field(min_length=1, max_length=256)


@router.get("", response_model=LibraryPageResponse)
async def browse_library(
    user: CurrentUser,
    session: Session,
    limit: Annotated[int, Query(ge=1, le=100)] = 48,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> LibraryPageResponse:
    rows = list(
        await session.execute(
            select(Media, MediaFile, PlaybackProgress)
            .join(MediaFile, (MediaFile.media_id == Media.id) & MediaFile.is_active.is_(True))
            .outerjoin(
                PlaybackProgress,
                (PlaybackProgress.media_id == Media.id) & (PlaybackProgress.user_id == user.id),
            )
            .order_by(Media.updated_at.desc(), Media.id.desc())
            .offset(offset)
            .limit(limit + 1)
        )
    )

    def item(
        media: Media, file: MediaFile, progress: PlaybackProgress | None
    ) -> LibraryItemResponse:
        return LibraryItemResponse(
            id=media.id,
            title=media.title,
            studio=media.studio,
            release_date=media.release_date.isoformat() if media.release_date else None,
            duration_seconds=file.duration_seconds,
            quality=file.quality,
            resolution=file.resolution,
            position_seconds=progress.position_seconds if progress else None,
            progress_duration_seconds=progress.duration_seconds if progress else None,
            completed=progress.completed if progress else False,
            poster_url=f"/api/media/{media.id}/poster",
            sprite_url=f"/api/media/{media.id}/sprite",
        )

    return LibraryPageResponse(
        items=[item(*row) for row in rows[:limit]],
        next_offset=offset + limit if len(rows) > limit else None,
    )


def library_item(media: Media, file: MediaFile, progress: PlaybackProgress | None) -> LibraryItemResponse:
    return LibraryItemResponse(
        id=media.id,
        title=media.title,
        studio=media.studio,
        release_date=media.release_date.isoformat() if media.release_date else None,
        duration_seconds=file.duration_seconds,
        quality=file.quality,
        resolution=file.resolution,
        position_seconds=progress.position_seconds if progress else None,
        progress_duration_seconds=progress.duration_seconds if progress else None,
        completed=progress.completed if progress else False,
        poster_url=f"/api/media/{media.id}/poster",
        sprite_url=f"/api/media/{media.id}/sprite",
    )


@home_router.get("", response_model=HomeResponse)
async def home(user: CurrentUser, session: Session) -> HomeResponse:
    rows = list(
        await session.execute(
            select(Media, MediaFile, PlaybackProgress)
            .join(MediaFile, (MediaFile.media_id == Media.id) & MediaFile.is_active.is_(True))
            .outerjoin(
                PlaybackProgress,
                (PlaybackProgress.media_id == Media.id) & (PlaybackProgress.user_id == user.id),
            )
            .order_by(Media.updated_at.desc(), Media.id.desc())
            .limit(24)
        )
    )
    items = [library_item(*row) for row in rows]
    return HomeResponse(
        continue_watching=[item for item in items if item.position_seconds is not None and not item.completed],
        recently_added=items[:12],
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
async def media_detail(media_id: UUID, _: CurrentUser, session: Session) -> MediaDetailResponse:
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
    return MediaDetailResponse(
        id=media.id,
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
