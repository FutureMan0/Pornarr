"""Administrator-only root-folder configuration."""

from __future__ import annotations

import os
import shutil
from datetime import datetime
from pathlib import Path
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from arq.jobs import Job
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from pornarr_api.auth import database_session, require_role
from pornarr_db.audit import write_audit
from pornarr_db.models.media import MediaFile
from pornarr_db.models.root_folders import RootFolder
from pornarr_db.models.user import User, UserRole
from pornarr_shared.errors import PornarrError
from pornarr_shared.jobs import IMPORT_QUEUE, job_key

router = APIRouter(prefix="/admin/library", tags=["admin"])
Admin = Annotated[User, Depends(require_role(UserRole.ADMIN))]
Session = Annotated[AsyncSession, Depends(database_session)]


class RootFolderWrite(BaseModel):
    path: Annotated[str, Field(min_length=1, max_length=1024)]
    enabled: bool = True


class RootFolderResponse(BaseModel):
    id: UUID
    path: str
    enabled: bool
    free_space_bytes: int
    total_space_bytes: int | None
    last_scanned_at: datetime | None
    last_space_checked_at: datetime | None
    low_space_warning_sent: bool
    same_filesystem_as_downloads: bool
    warning: str | None


class ScanResponse(BaseModel):
    job_id: str


class RootFolderValidationError(PornarrError):
    code = "ROOT_FOLDER_INVALID"
    status = 422


class RootFolderInUseError(PornarrError):
    code = "ROOT_FOLDER_HAS_MEDIA"
    status = 409


@router.get("/root-folders", response_model=list[RootFolderResponse])
async def list_root_folders(
    request: Request, _: Admin, session: Session
) -> list[RootFolderResponse]:
    folders = await session.scalars(select(RootFolder).order_by(RootFolder.path))
    return [
        _folder_response(folder, request.app.state.settings.torrents_path) for folder in folders
    ]


@router.post("/root-folders", response_model=RootFolderResponse, status_code=201)
async def create_root_folder(
    payload: RootFolderWrite, request: Request, user: Admin, session: Session
) -> RootFolderResponse:
    path, free_space_bytes, same_filesystem = _validate_root_folder(
        payload.path, request.app.state.settings.torrents_path
    )
    existing = await session.scalar(select(RootFolder).where(RootFolder.path == str(path)))
    if existing is not None:
        raise RootFolderValidationError(
            "The root folder is already configured.", reason="duplicate"
        )
    folder = RootFolder(
        path=str(path),
        enabled=payload.enabled,
        free_space_bytes=free_space_bytes,
    )
    session.add(folder)
    await session.flush()
    write_audit(session, actor_id=user.id, action="root_folder.created", target=str(folder.id))
    return _folder_response(folder, request.app.state.settings.torrents_path, same_filesystem)


@router.delete("/root-folders/{folder_id}", status_code=204)
async def delete_root_folder(folder_id: UUID, user: Admin, session: Session) -> None:
    folder = await session.get(RootFolder, folder_id)
    if folder is None:
        raise HTTPException(status_code=404)
    media_file = await session.scalar(
        select(MediaFile.id)
        .where(MediaFile.path.startswith(f"{folder.path.rstrip('/')}/"))
        .limit(1)
    )
    if media_file is not None:
        raise RootFolderInUseError("The root folder still contains media.")
    await session.delete(folder)
    write_audit(session, actor_id=user.id, action="root_folder.deleted", target=str(folder_id))


@router.post("/root-folders/{folder_id}/scan", response_model=ScanResponse, status_code=202)
async def scan_root_folder(
    folder_id: UUID, request: Request, _: Admin, session: Session
) -> ScanResponse:
    folder = await session.get(RootFolder, folder_id)
    if folder is None:
        raise HTTPException(status_code=404)
    if not folder.enabled:
        raise RootFolderValidationError("The root folder is disabled.", reason="disabled")
    job_id = job_key("scan", str(folder_id), queue=IMPORT_QUEUE)
    await request.app.state.redis.enqueue_job(
        "scan", str(folder_id), _job_id=job_id, _queue_name=IMPORT_QUEUE
    )
    return ScanResponse(job_id=job_id)


@router.delete("/root-folders/{folder_id}/scan/{job_id}", status_code=202)
async def cancel_root_folder_scan(
    folder_id: UUID, job_id: str, request: Request, _: Admin, session: Session
) -> None:
    folder = await session.get(RootFolder, folder_id)
    if folder is None:
        raise HTTPException(status_code=404)
    expected_job_id = job_key("scan", str(folder_id), queue=IMPORT_QUEUE)
    if job_id != expected_job_id:
        raise HTTPException(status_code=404)
    await Job(job_id, request.app.state.redis, _queue_name=IMPORT_QUEUE).abort(timeout=0)


def _validate_root_folder(path_value: str, downloads_path: Path) -> tuple[Path, int, bool]:
    try:
        path = Path(path_value).expanduser().resolve(strict=True)
    except OSError as error:
        raise RootFolderValidationError(
            "The root folder does not exist.", reason="missing"
        ) from error
    if not path.is_dir():
        raise RootFolderValidationError(
            "The root folder is not a directory.", reason="not_directory"
        )
    if not os.access(path, os.R_OK):
        raise RootFolderValidationError("The root folder is not readable.", reason="not_readable")
    if not os.access(path, os.W_OK):
        raise RootFolderValidationError("The root folder is not writable.", reason="not_writable")
    try:
        free_space_bytes = shutil.disk_usage(path).free
        same_filesystem = path.stat().st_dev == downloads_path.stat().st_dev
    except OSError as error:
        raise RootFolderValidationError(
            "The root folder is unavailable.", reason="unavailable"
        ) from error
    return path, free_space_bytes, same_filesystem


def _folder_response(
    folder: RootFolder, downloads_path: Path, same_filesystem: bool | None = None
) -> RootFolderResponse:
    same_filesystem = (
        Path(folder.path).stat().st_dev == downloads_path.stat().st_dev
        if same_filesystem is None
        else same_filesystem
    )
    warning = (
        None if same_filesystem else "different filesystem from downloads; imports cannot hardlink"
    )
    return RootFolderResponse(
        id=folder.id,
        path=folder.path,
        enabled=folder.enabled,
        free_space_bytes=folder.free_space_bytes,
        total_space_bytes=folder.total_space_bytes,
        last_scanned_at=folder.last_scanned_at,
        last_space_checked_at=folder.last_space_checked_at,
        low_space_warning_sent=folder.low_space_warning_sent,
        same_filesystem_as_downloads=same_filesystem,
        warning=warning,
    )
