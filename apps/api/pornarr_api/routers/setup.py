"""One-time first-run setup."""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field, SecretStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from pornarr_api.auth import database_session, hash_password
from pornarr_api.errors import ErrorResponse
from pornarr_db.models.filters import (
    ContentFilterProfile,
    ContentFilterRule,
    FilterAction,
    FilterProfileScope,
    FilterRuleKind,
)
from pornarr_db.models.root_folders import RootFolder
from pornarr_db.models.user import User, UserRole
from pornarr_shared.errors import PornarrError

router = APIRouter(prefix="/setup", tags=["setup"])
Session = Annotated[AsyncSession, Depends(database_session)]
SETUP_ERRORS: dict[int | str, dict[str, Any]] = {
    409: {"model": ErrorResponse},
    422: {"model": ErrorResponse},
}


class SetupAlreadyCompletedError(PornarrError):
    code = "SETUP_ALREADY_COMPLETED"
    status = 409


class SetupPathInvalidError(PornarrError):
    code = "SETUP_PATH_INVALID"
    status = 422


class SetupWrite(BaseModel):
    username: Annotated[str, Field(min_length=1, max_length=64)]
    password: SecretStr
    library_path: Annotated[str, Field(min_length=1, max_length=1024)]


class SetupCompleteResponse(BaseModel):
    username: str
    same_filesystem_as_downloads: bool
    warning: str | None


@router.get("/status")
async def setup_status(session: Session) -> dict[str, bool]:
    return {"configured": await session.scalar(select(User.id).limit(1)) is not None}


@router.post(
    "/complete", response_model=SetupCompleteResponse, status_code=201, responses=SETUP_ERRORS
)
async def complete_setup(
    payload: SetupWrite, request: Request, session: Session
) -> SetupCompleteResponse:
    profile = await session.scalar(
        select(ContentFilterProfile)
        .where(ContentFilterProfile.scope == FilterProfileScope.GLOBAL)
        .with_for_update()
    )
    if await session.scalar(select(User.id).limit(1)) is not None:
        raise SetupAlreadyCompletedError("The instance has already been configured.")
    path, free_space_bytes, same_filesystem = _validate_library_path(
        payload.library_path, request.app.state.settings.torrents_path
    )
    if profile is None:
        profile = ContentFilterProfile(scope=FilterProfileScope.GLOBAL)
        session.add(profile)
        profile.rules = [
            ContentFilterRule(kind=kind, pattern="", action=FilterAction.REJECT, enabled=False)
            for kind in FilterRuleKind
        ]
    admin = User(
        username=payload.username,
        password_hash=hash_password(payload.password.get_secret_value()),
        role=UserRole.ADMIN,
    )
    session.add(admin)
    session.add(RootFolder(path=str(path), enabled=True, free_space_bytes=free_space_bytes))
    warning = (
        None if same_filesystem else "different filesystem from downloads; imports cannot hardlink"
    )
    return SetupCompleteResponse(
        username=admin.username,
        same_filesystem_as_downloads=same_filesystem,
        warning=warning,
    )


def _validate_library_path(value: str, torrents_path: Path) -> tuple[Path, int, bool]:
    try:
        path = Path(value).expanduser().resolve(strict=True)
    except OSError as exc:
        raise SetupPathInvalidError(
            "The library path is unavailable.", reason="unavailable"
        ) from exc
    if not path.is_dir():
        raise SetupPathInvalidError("The library path is not a directory.", reason="not_directory")
    if not os.access(path, os.R_OK | os.W_OK):
        raise SetupPathInvalidError(
            "The library path must be readable and writable.", reason="not_accessible"
        )
    try:
        return path, shutil.disk_usage(path).free, path.stat().st_dev == torrents_path.stat().st_dev
    except OSError as exc:
        raise SetupPathInvalidError(
            "The library path is unavailable.", reason="unavailable"
        ) from exc
