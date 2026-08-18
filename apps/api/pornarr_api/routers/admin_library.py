"""Administrator-only root-folder configuration."""

from __future__ import annotations

import os
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from pornarr_api.auth import database_session, require_role
from pornarr_api.errors import ErrorResponse
from pornarr_db.audit import write_audit
from pornarr_db.models.media import MediaFile
from pornarr_db.models.root_folders import RootFolder
from pornarr_db.models.user import User, UserRole
from pornarr_shared.errors import PornarrError
from pornarr_shared.jobs import IMPORT_QUEUE, SCAN_JOB_NAME, enqueue_once

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


class RootFolderValidationError(PornarrError):
    code = "ROOT_FOLDER_INVALID"
    status = 422


class RootFolderInUseError(PornarrError):
    code = "ROOT_FOLDER_HAS_MEDIA"
    status = 409


class RootFolderDisabledError(PornarrError):
    code = "ROOT_FOLDER_DISABLED"
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


@router.post(
    "/root-folders/{folder_id}/scan",
    status_code=202,
    response_class=Response,
    responses={404: {"model": ErrorResponse}, 409: {"model": ErrorResponse}},
)
async def scan_root_folder(
    folder_id: UUID, request: Request, user: Admin, session: Session
) -> Response:
    """Ask the worker to walk one root folder now, so its media can enter the library.

    The progress is not returned here. The worker publishes `scan.progress` on
    the event stream as it goes, and the screen watches that; holding the
    request open for a walk of ten thousand files would time out long before it
    told anyone anything.
    """

    folder = await session.get(RootFolder, folder_id)
    if folder is None:
        raise HTTPException(status_code=404)
    if not folder.enabled:
        # A disabled folder is one the household has switched off. Scanning it
        # anyway would quietly reimport what somebody chose to exclude.
        raise RootFolderDisabledError("The root folder is disabled.")
    # `enqueue_once` keys on the arguments, so an administrator clicking twice
    # gets one walk rather than two racing over the same files. The minute
    # stamp is what lets them scan again once the previous run finished, while
    # still collapsing the impatient double click.
    minute = datetime.now(UTC).replace(second=0, microsecond=0).isoformat()
    # The import queue, not the default one: `scan` is registered on the import
    # worker, and a job on a queue nothing listens to waits forever without
    # anyone being told.
    await enqueue_once(
        request.app.state.queue,
        SCAN_JOB_NAME,
        str(folder_id),
        f"manual:{minute}",
        queue=IMPORT_QUEUE,
    )
    write_audit(
        session,
        actor_id=user.id,
        action="root_folder.scanned",
        target=str(folder_id),
        context={"path": folder.path},
    )
    await session.flush()
    return Response(status_code=202)


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
    """One folder, and whether an import from downloads can hardlink into it.

    A missing path — either side — is reported rather than raised. Nothing
    creates the download directories, so a fresh deployment has a root folder
    and no `/data/torrents`, and answering 500 there makes the whole screen
    unreachable over a condition it exists to describe. Treated as "not the same
    filesystem", which is the cautious reading: it says imports will copy, and
    copying always works.
    """
    if same_filesystem is None:
        try:
            same_filesystem = Path(folder.path).stat().st_dev == downloads_path.stat().st_dev
        except OSError:
            same_filesystem = False

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
