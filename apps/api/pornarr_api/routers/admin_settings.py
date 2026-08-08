"""Administrator-managed runtime configuration."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from pornarr_api.auth import database_session, require_role
from pornarr_api.errors import ErrorResponse
from pornarr_db.audit import write_audit
from pornarr_db.models.user import User, UserRole
from pornarr_db.settings import (
    RuntimeSettings,
    RuntimeSettingsWrite,
    get_runtime_settings,
    update_runtime_settings,
)
from pornarr_shared.events import publish_event

router = APIRouter(prefix="/admin/settings", tags=["admin"])
Admin = Annotated[User, Depends(require_role(UserRole.ADMIN))]
Session = Annotated[AsyncSession, Depends(database_session)]


@router.get("", response_model=RuntimeSettings)
async def read_settings(request: Request, _: Admin, session: Session) -> RuntimeSettings:
    return await get_runtime_settings(session, request.app.state.settings)


@router.patch("", response_model=RuntimeSettings, responses={422: {"model": ErrorResponse}})
async def write_settings(
    payload: RuntimeSettingsWrite, request: Request, user: Admin, session: Session
) -> RuntimeSettings:
    settings = await update_runtime_settings(session, request.app.state.settings, payload)
    changed_keys = sorted(payload.model_fields_set)
    write_audit(session, actor_id=user.id, action="settings.updated", target=",".join(changed_keys))
    await publish_event(request.app.state.redis, "settings.changed", {"keys": changed_keys})
    return settings
