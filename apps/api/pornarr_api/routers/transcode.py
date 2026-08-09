"""Heartbeat and administration routes for active HLS transcodes."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel

from pornarr_api.auth import ForbiddenError, get_current_user, require_role
from pornarr_api.errors import ErrorResponse
from pornarr_db.models.user import User, UserRole
from pornarr_media.capabilities import HardwareCapabilities
from pornarr_media.sessions import TranscodeLimits, TranscodeSession, TranscodeSessionRegistry

router = APIRouter(prefix="/transcode", tags=["transcode"])
admin_router = APIRouter(prefix="/admin/transcode", tags=["admin"])
Admin = Annotated[User, Depends(require_role(UserRole.ADMIN))]


class TranscodeSessionResponse(BaseModel):
    id: UUID
    user_id: UUID
    media_id: UUID
    mode: str
    created_at: datetime


class TranscodeLimitResponse(BaseModel):
    hardware: int
    software: int
    per_user: int
    hardware_in_use: int
    software_in_use: int


def get_registry(request: Request) -> TranscodeSessionRegistry:
    registry = getattr(request.app.state, "transcode_sessions", None)
    if registry is None:
        registry = TranscodeSessionRegistry(
            request.app.state.redis, request.app.state.settings.transcode_path
        )
        request.app.state.transcode_sessions = registry
    return registry


def session_response(session: TranscodeSession) -> TranscodeSessionResponse:
    return TranscodeSessionResponse(
        id=session.id,
        user_id=session.user_id,
        media_id=session.media_id,
        mode=session.mode,
        created_at=session.created_at,
    )


@router.post(
    "/sessions/{session_id}/heartbeat",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={403: {"model": ErrorResponse}, 404: {"model": ErrorResponse}},
)
async def heartbeat(
    session_id: UUID,
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
) -> None:
    session = await get_registry(request).get(session_id)
    if session is None:
        raise HTTPException(status_code=404)
    if session.user_id != user.id:
        raise ForbiddenError("Only the session owner can refresh it.")
    await get_registry(request).heartbeat(session_id, user.id)


@admin_router.get("/sessions", response_model=list[TranscodeSessionResponse])
async def list_sessions(request: Request, _: Admin) -> list[TranscodeSessionResponse]:
    return [session_response(session) for session in await get_registry(request).active_sessions()]


@admin_router.get("/limits", response_model=TranscodeLimitResponse)
async def limit_state(request: Request, _: Admin) -> TranscodeLimitResponse:
    sessions = await get_registry(request).active_sessions()
    capabilities = getattr(
        request.app.state,
        "hardware_capabilities",
        HardwareCapabilities(methods=(), rejections=(), nvidia_gpus=()),
    )
    limits = TranscodeLimits.from_settings(request.app.state.settings, capabilities)
    return TranscodeLimitResponse(
        hardware=limits.hardware,
        software=limits.software,
        per_user=limits.per_user,
        hardware_in_use=sum(session.hardware for session in sessions),
        software_in_use=sum(not session.hardware for session in sessions),
    )


@admin_router.delete(
    "/sessions/{session_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={404: {"model": ErrorResponse}},
)
async def terminate_session(session_id: UUID, request: Request, _: Admin) -> None:
    if not await get_registry(request).terminate(session_id):
        raise HTTPException(status_code=404)
