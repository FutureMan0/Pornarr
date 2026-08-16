"""Heartbeat and administration routes for active HLS transcodes."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from pornarr_api.auth import ForbiddenError, database_session, get_current_user, require_role
from pornarr_api.errors import ErrorResponse
from pornarr_db.models.media import Media, MediaFile
from pornarr_db.models.user import User, UserRole
from pornarr_media.capabilities import HardwareCapabilities
from pornarr_media.sessions import (
    TranscodeFailure,
    TranscodeLimits,
    TranscodeSession,
    TranscodeSessionRegistry,
)
from pornarr_media.transcode import start_hls_transcode

router = APIRouter(prefix="/transcode", tags=["transcode"])
admin_router = APIRouter(prefix="/admin/transcode", tags=["admin"])
Admin = Annotated[User, Depends(require_role(UserRole.ADMIN))]
Session = Annotated[AsyncSession, Depends(database_session)]


class TranscodeSessionResponse(BaseModel):
    id: UUID
    user_id: UUID
    media_id: UUID
    username: str | None
    media_title: str | None
    mode: str
    hardware: bool
    created_at: datetime
    elapsed_seconds: int


class TranscodeLimitResponse(BaseModel):
    hardware: int
    software: int
    per_user: int
    hardware_in_use: int
    software_in_use: int
    configured_hardware: int | None
    configured_software: int | None
    configured_per_user: int
    effective_hardware: int
    effective_software: int
    effective_per_user: int


class CodecCapabilityResponse(BaseModel):
    codec: str
    maximum_tested_resolution: str


class HardwareCapabilityResponse(BaseModel):
    acceleration: str
    codecs: list[CodecCapabilityResponse]


class HardwareRejectionResponse(BaseModel):
    acceleration: str | None
    reason: str


class HardwareCapabilitiesResponse(BaseModel):
    methods: list[HardwareCapabilityResponse]
    rejections: list[HardwareRejectionResponse]
    nvidia_gpus: list[str]


class TranscodeFailureResponse(BaseModel):
    session_id: UUID
    user_id: UUID
    media_id: UUID
    mode: str
    exit_code: int
    reason: str
    created_at: datetime


class TranscodeStartResponse(BaseModel):
    session_id: UUID
    playlist_url: str


def get_registry(request: Request) -> TranscodeSessionRegistry:
    registry = getattr(request.app.state, "transcode_sessions", None)
    if registry is None:
        registry = TranscodeSessionRegistry(
            request.app.state.redis, request.app.state.settings.transcode_path
        )
        request.app.state.transcode_sessions = registry
    return registry


def session_response(
    session: TranscodeSession, usernames: dict[UUID, str], media_titles: dict[UUID, str]
) -> TranscodeSessionResponse:
    return TranscodeSessionResponse(
        id=session.id,
        user_id=session.user_id,
        media_id=session.media_id,
        username=usernames.get(session.user_id),
        media_title=media_titles.get(session.media_id),
        mode=session.mode,
        hardware=session.hardware,
        created_at=session.created_at,
        elapsed_seconds=max(0, int((datetime.now(UTC) - session.created_at).total_seconds())),
    )


def capabilities_response(capabilities: HardwareCapabilities) -> HardwareCapabilitiesResponse:
    return HardwareCapabilitiesResponse(
        methods=[
            HardwareCapabilityResponse(
                acceleration=method.acceleration,
                codecs=[
                    CodecCapabilityResponse(
                        codec=codec.codec,
                        maximum_tested_resolution=codec.maximum_tested_resolution,
                    )
                    for codec in method.codecs
                ],
            )
            for method in capabilities.methods
        ],
        rejections=[
            HardwareRejectionResponse(
                acceleration=rejection.acceleration,
                reason=rejection.reason,
            )
            for rejection in capabilities.rejections
        ],
        nvidia_gpus=list(capabilities.nvidia_gpus),
    )


def failure_response(failure: TranscodeFailure) -> TranscodeFailureResponse:
    return TranscodeFailureResponse(
        session_id=failure.session_id,
        user_id=failure.user_id,
        media_id=failure.media_id,
        mode=failure.mode,
        exit_code=failure.exit_code,
        reason=failure.reason,
        created_at=failure.created_at,
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


@router.delete("/sessions/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
async def stop_session(
    session_id: UUID, request: Request, user: Annotated[User, Depends(get_current_user)]
) -> None:
    session = await get_registry(request).get(session_id)
    if session is None:
        raise HTTPException(status_code=404)
    if session.user_id != user.id:
        raise ForbiddenError("Only the session owner can stop it.")
    await get_registry(request).terminate(session_id)


def _hls_asset_path(request: Request, session_id: UUID, asset: str) -> Path | None:
    valid_segment = asset.startswith("segment_") and asset.endswith(".ts") and asset[8:-3].isdigit()
    if asset not in {"master.m3u8", "variant.m3u8"} and not valid_segment:
        return None
    path = request.app.state.settings.transcode_path / str(session_id) / asset
    return path if path.is_file() else None


@router.get("/sessions/{session_id}/hls/{asset}")
async def hls_asset(
    session_id: UUID,
    asset: str,
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
) -> FileResponse:
    session = await get_registry(request).get(session_id)
    if session is None:
        raise HTTPException(status_code=404)
    if session.user_id != user.id:
        raise ForbiddenError("Only the session owner can view its stream.")
    path = _hls_asset_path(request, session_id, asset)
    if path is None:
        raise HTTPException(status_code=404)
    media_type = "application/vnd.apple.mpegurl" if asset.endswith(".m3u8") else "video/mp2t"
    return FileResponse(path, media_type=media_type)


@router.post("/media/{media_id}/sessions", response_model=TranscodeStartResponse, status_code=201)
async def start_session(
    media_id: UUID,
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
    session: Session,
) -> TranscodeStartResponse:
    media_file = await session.scalar(
        select(MediaFile).where(MediaFile.media_id == media_id, MediaFile.is_active.is_(True))
    )
    if media_file is None:
        raise HTTPException(status_code=404)
    source = Path(media_file.path).resolve()
    if not source.is_relative_to(request.app.state.settings.library_path.resolve()) or not source.is_file():
        raise HTTPException(status_code=404)

    registry = get_registry(request)
    capabilities = getattr(request.app.state, "hardware_capabilities", HardwareCapabilities((), (), ()))
    limits = TranscodeLimits.from_settings(request.app.state.settings, capabilities)
    mode = await registry.select_mode(user.id, limits)
    capability = capabilities.methods[0] if mode.value == "hardware" and capabilities.methods else None
    if mode.value == "hardware" and capability is None:
        limits = TranscodeLimits(0, limits.software, limits.per_user)
        mode = await registry.select_mode(user.id, limits)
    session_id = uuid4()
    transcode = await start_hls_transcode(
        source,
        request.app.state.settings.transcode_path,
        session_id,
        acceleration=capability.acceleration if capability else None,
        device=capability.device if capability else None,
    )
    await registry.register(
        session_id, user.id, media_id, "hls", transcode, hardware=capability is not None
    )
    return TranscodeStartResponse(
        session_id=session_id,
        playlist_url=f"/api/transcode/sessions/{session_id}/hls/master.m3u8",
    )


@admin_router.get("/sessions", response_model=list[TranscodeSessionResponse])
async def list_sessions(
    request: Request, _: Admin, session: Session
) -> list[TranscodeSessionResponse]:
    sessions = await get_registry(request).active_sessions()
    user_ids = {active_session.user_id for active_session in sessions}
    media_ids = {active_session.media_id for active_session in sessions}
    usernames = {
        user.id: user.username
        for user in await session.scalars(select(User).where(User.id.in_(user_ids)))
    }
    media_titles = {
        media.id: media.title
        for media in await session.scalars(select(Media).where(Media.id.in_(media_ids)))
    }
    return [
        session_response(active_session, usernames, media_titles) for active_session in sessions
    ]


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
        configured_hardware=request.app.state.settings.transcode_max_hw_sessions,
        configured_software=request.app.state.settings.transcode_max_sw_sessions,
        configured_per_user=request.app.state.settings.transcode_max_per_user,
        effective_hardware=limits.hardware,
        effective_software=limits.software,
        effective_per_user=limits.per_user,
    )


@admin_router.get("/capabilities", response_model=HardwareCapabilitiesResponse)
async def capabilities(request: Request, _: Admin) -> HardwareCapabilitiesResponse:
    return capabilities_response(
        getattr(
            request.app.state,
            "hardware_capabilities",
            HardwareCapabilities(methods=(), rejections=(), nvidia_gpus=()),
        )
    )


@admin_router.get("/failures", response_model=list[TranscodeFailureResponse])
async def recent_failures(request: Request, _: Admin) -> list[TranscodeFailureResponse]:
    return [failure_response(failure) for failure in await get_registry(request).recent_failures()]


@admin_router.delete(
    "/sessions/{session_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={404: {"model": ErrorResponse}},
)
async def terminate_session(session_id: UUID, request: Request, _: Admin) -> None:
    if not await get_registry(request).terminate(session_id):
        raise HTTPException(status_code=404)
