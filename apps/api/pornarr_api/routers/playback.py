"""Playback: direct-play decisions, plus progress and resume.

Two routers, because the two feature sets sit under different prefixes and
merging them would change one of their URLs: `router` serves /media for the
direct-play decision, `progress_router` serves /playback for progress and
resume. `transcode.py` exports two routers for the same reason.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Annotated, Self
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from pornarr_api.auth import database_session, get_current_user
from pornarr_api.errors import ErrorResponse
from pornarr_core.playback import (
    DEFAULT_CLIENT_CAPABILITIES,
    ClientCapabilities,
    DirectPlayDecision,
    DirectPlayReason,
    PlaybackSource,
    decide_direct_play,
)
from pornarr_db.events import record_user_event
from pornarr_db.models.media import Media, MediaFile
from pornarr_db.models.playback import PlaybackProgress, UserEventType
from pornarr_db.models.user import User
from pornarr_db.settings import get_runtime_settings

router = APIRouter(prefix="/media", tags=["playback"])
progress_router = APIRouter(prefix="/playback", tags=["playback"])
CurrentUser = Annotated[User, Depends(get_current_user)]
Session = Annotated[AsyncSession, Depends(database_session)]


class PlaybackInfoResponse(BaseModel):
    direct_play: bool
    reasons: tuple[DirectPlayReason, ...]


async def client_capabilities(
    containers: Annotated[list[str] | None, Query()] = None,
    video_codecs: Annotated[list[str] | None, Query()] = None,
    audio_codecs: Annotated[list[str] | None, Query()] = None,
    video_profiles: Annotated[list[str] | None, Query()] = None,
    maximum_video_level: Annotated[float | None, Query(ge=0)] = None,
) -> ClientCapabilities:
    """Build an explicit player matrix, or preserve the safe default."""
    if all(
        value is None
        for value in (
            containers,
            video_codecs,
            audio_codecs,
            video_profiles,
            maximum_video_level,
        )
    ):
        return DEFAULT_CLIENT_CAPABILITIES
    return ClientCapabilities(
        containers=frozenset(containers or ()),
        video_codecs=frozenset(video_codecs or ()),
        audio_codecs=frozenset(audio_codecs or ()),
        video_profiles=frozenset(video_profiles or ()),
        maximum_video_level=maximum_video_level or 0,
    )


def _source(technical_metadata: dict[str, object] | None) -> PlaybackSource:
    metadata: Mapping[str, object] = technical_metadata or {}
    video = _mapping(metadata.get("video"))
    audio = _mapping(metadata.get("audio"))
    return PlaybackSource(
        container=_string(metadata.get("container")),
        video_codec=_string(video.get("codec")),
        audio_codec=_string(audio.get("codec")),
        video_profile=_string(video.get("profile")),
        video_level=_number(video.get("level")),
    )


def _mapping(value: object) -> Mapping[str, object]:
    return value if isinstance(value, dict) else {}


def _string(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _number(value: object) -> float | None:
    if not isinstance(value, (int, float, str)):
        return None
    try:
        return float(value)
    except ValueError:
        return None


def _response(decision: DirectPlayDecision) -> PlaybackInfoResponse:
    return PlaybackInfoResponse(direct_play=decision.direct_play, reasons=decision.reasons)


@router.get(
    "/{media_id}/playback-info",
    response_model=PlaybackInfoResponse,
    responses={
        401: {"model": ErrorResponse},
        404: {"model": ErrorResponse},
        422: {"model": ErrorResponse},
    },
)
async def playback_info(
    media_id: UUID,
    user: CurrentUser,
    session: Session,
    capabilities: Annotated[ClientCapabilities, Depends(client_capabilities)],
) -> PlaybackInfoResponse:
    media_file = await session.scalar(
        select(MediaFile).where(MediaFile.media_id == media_id, MediaFile.is_active.is_(True))
    )
    if media_file is None:
        raise HTTPException(status_code=404)
    await record_user_event(session, user.id, UserEventType.VIEW, media_id=media_id)
    return _response(decide_direct_play(_source(media_file.codecs), capabilities))


class PlaybackProgressWrite(BaseModel):
    device_label: Annotated[str | None, Field(max_length=64)] = None
    position_seconds: Annotated[float, Field(ge=0)]
    duration_seconds: Annotated[float, Field(gt=0)]

    @model_validator(mode="after")
    def position_is_within_duration(self) -> Self:
        if self.position_seconds > self.duration_seconds:
            raise ValueError("position_seconds must not exceed duration_seconds")
        return self


class PlaybackProgressResponse(BaseModel):
    device_label: str | None
    media_id: UUID
    # Resolved for the caller. A resume list that shows identifiers is a resume
    # list nobody can use, and making every client fetch the title separately
    # turns one screen into one request per row.
    title: str | None
    position_seconds: float
    duration_seconds: float
    completed: bool


def progress_response(
    progress: PlaybackProgress, title: str | None = None
) -> PlaybackProgressResponse:
    return PlaybackProgressResponse(
        device_label=progress.device_label,
        media_id=progress.media_id,
        title=title,
        position_seconds=progress.position_seconds,
        duration_seconds=progress.duration_seconds,
        completed=progress.completed,
    )


async def progress_for_user(
    session: AsyncSession, user_id: UUID, media_id: UUID
) -> PlaybackProgress | None:
    return await session.scalar(
        select(PlaybackProgress).where(
            PlaybackProgress.user_id == user_id, PlaybackProgress.media_id == media_id
        )
    )


@progress_router.post("/{media_id}/progress", response_model=PlaybackProgressResponse)
async def report_progress(
    media_id: UUID,
    payload: PlaybackProgressWrite,
    request: Request,
    user: CurrentUser,
    session: Session,
) -> PlaybackProgressResponse:
    if await session.get(Media, media_id) is None:
        raise HTTPException(status_code=404)

    progress = await progress_for_user(session, user.id, media_id)
    settings = await get_runtime_settings(session, request.app.state.settings)
    reached_threshold = (
        payload.position_seconds * 100
        >= payload.duration_seconds * settings.playback_completion_threshold_percent
    )
    is_new_progress = progress is None
    was_completed = progress.completed if progress is not None else False
    if progress is None:
        progress = PlaybackProgress(
            user_id=user.id,
            media_id=media_id,
            position_seconds=payload.position_seconds,
            duration_seconds=payload.duration_seconds,
            completed=reached_threshold,
            completed_at=datetime.now(UTC) if reached_threshold else None,
            device_label=payload.device_label,
        )
        session.add(progress)
    else:
        progress.position_seconds = payload.position_seconds
        progress.duration_seconds = payload.duration_seconds
        # Only overwrite when the client actually names itself, or picking a
        # title up on a device that does not would blank the label.
        if payload.device_label is not None:
            progress.device_label = payload.device_label
        if reached_threshold and not progress.completed:
            progress.completed = True
            progress.completed_at = datetime.now(UTC)

    if is_new_progress:
        await record_user_event(session, user.id, UserEventType.PLAY, media_id=media_id)
    await record_user_event(
        session,
        user.id,
        UserEventType.PROGRESS,
        media_id=media_id,
        value=payload.position_seconds * 100 / payload.duration_seconds,
    )
    if reached_threshold and not was_completed:
        await record_user_event(session, user.id, UserEventType.COMPLETED, media_id=media_id)

    await session.flush()
    return progress_response(progress)


@progress_router.get("/{media_id}/progress", response_model=PlaybackProgressResponse)
async def playback_progress(
    media_id: UUID, user: CurrentUser, session: Session
) -> PlaybackProgressResponse:
    progress = await progress_for_user(session, user.id, media_id)
    if progress is None:
        raise HTTPException(status_code=404)
    return progress_response(progress)


@progress_router.get("/continue-watching", response_model=list[PlaybackProgressResponse])
async def continue_watching(user: CurrentUser, session: Session) -> list[PlaybackProgressResponse]:
    rows = list(
        await session.execute(
            select(PlaybackProgress, Media.title)
            .join(Media, Media.id == PlaybackProgress.media_id)
            .where(PlaybackProgress.user_id == user.id, PlaybackProgress.completed.is_(False))
            .order_by(PlaybackProgress.updated_at.desc())
        )
    )
    return [progress_response(item, title) for item, title in rows]


class PlayingOnResponse(BaseModel):
    """One device this account currently has a transcode running on."""

    session_id: UUID
    media_id: UUID
    media_title: str
    device_label: str | None
    mode: str
    hardware: bool
    started_at: datetime


@progress_router.get("/sessions/mine", response_model=list[PlayingOnResponse])
async def my_sessions(
    request: Request, user: CurrentUser, session: Session
) -> list[PlayingOnResponse]:
    """What "Playing on" reads: this account's live transcodes.

    Direct play deliberately does not appear. A direct-playing client streams
    the file without asking the server to keep any session state, so there is
    nothing here to report — the absence of a row for a device *is* the signal
    that it is not transcoding, and inventing a record would mean tracking
    playback the server otherwise has no reason to know about.
    """
    registry = getattr(request.app.state, "transcode_sessions", None)
    if registry is None:
        return []
    mine = [active for active in await registry.active_sessions() if active.user_id == user.id]
    titles = {
        media.id: media.title
        for media in await session.scalars(
            select(Media).where(Media.id.in_({active.media_id for active in mine}))
        )
    }
    return [
        PlayingOnResponse(
            session_id=active.id,
            media_id=active.media_id,
            media_title=titles.get(active.media_id, ""),
            device_label=active.device_label,
            mode=active.mode,
            hardware=active.hardware,
            started_at=active.created_at,
        )
        for active in sorted(mine, key=lambda active: active.created_at, reverse=True)
    ]
