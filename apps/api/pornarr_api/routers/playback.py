"""Playback metadata for direct-play decisions."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
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
from pornarr_db.models.media import MediaFile
from pornarr_db.models.user import User

router = APIRouter(prefix="/media", tags=["playback"])
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
    _: CurrentUser,
    session: Session,
    capabilities: Annotated[ClientCapabilities, Depends(client_capabilities)],
) -> PlaybackInfoResponse:
    media_file = await session.scalar(
        select(MediaFile).where(MediaFile.media_id == media_id, MediaFile.is_active.is_(True))
    )
    if media_file is None:
        raise HTTPException(status_code=404)
    return _response(decide_direct_play(_source(media_file.codecs), capabilities))
