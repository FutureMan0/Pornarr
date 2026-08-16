"""A1 — what the administrator sees first.

FOUR NUMBERS AND FOUR SHARES, EACH FROM SOMETHING REAL. The temptation on a
dashboard is to show whatever looks impressive; the discipline is to show only
what the database can actually answer. Where the data does not exist the field
is absent rather than zero, because a zero on a dashboard is read as a fact.

Two are worth being explicit about:

- **Metadata matched** counts titles a provider actually scored — those with a
  confidence at all. There is no configurable threshold in this application, so
  applying one here would be inventing a standard and then measuring against
  it. A title imported with no provider hit has no confidence and is not
  matched, which is exactly what this bar exists to make visible.
- **Artwork present** is a directory listing, not a stat per title. Posters live
  under `thumbnail_path/<media id>/`, so one `scandir` answers for the whole
  library; asking the filesystem once per title would make the dashboard the
  slowest screen in the application.

`duplicates_flagged` is a count of files, not a share, and is reported as such.
The others are shares of the library and only mean anything against its size,
which is why `total` travels with them.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from pornarr_api.auth import database_session, require_role
from pornarr_db.models.entities import MediaTag
from pornarr_db.models.media import Media
from pornarr_db.models.quarantine import QuarantineItem
from pornarr_db.models.root_folders import RootFolder
from pornarr_db.models.user import User, UserRole

router = APIRouter(prefix="/admin", tags=["admin"])
CurrentAdmin = Annotated[User, Depends(require_role(UserRole.ADMIN))]
Session = Annotated[AsyncSession, Depends(database_session)]

RECENT_WINDOW = timedelta(days=7)


class LibraryHealth(BaseModel):
    """Shares of the library, each out of `total`."""

    total: int
    metadata_matched: int
    artwork_present: int
    tagged: int
    # A count of files rather than a share: a duplicate is a thing to deal with,
    # not a proportion of the library to feel good about.
    duplicates_flagged: int


class StorageTotals(BaseModel):
    # Absent when no root folder has reported its filesystem yet. Nothing is
    # more misleading on a storage card than a confident "0 B of 0 B".
    used_bytes: int | None
    total_bytes: int | None
    volumes: int


class OverviewResponse(BaseModel):
    titles: int
    titles_added_this_week: int
    untagged: int
    guests: int
    storage: StorageTotals
    health: LibraryHealth
    last_scan_at: datetime | None


async def _count(session: AsyncSession, statement: Select[tuple[int]]) -> int:
    return int((await session.scalar(statement)) or 0)


def _artwork_present(thumbnail_path: os.PathLike[str] | str) -> int:
    """How many titles have a poster, from one directory listing.

    A missing directory means no artwork has been produced yet, which is a
    normal state for a fresh server and not an error.
    """
    try:
        with os.scandir(thumbnail_path) as entries:
            return sum(
                1
                for entry in entries
                if entry.is_dir() and (Path(entry.path) / "poster.jpg").is_file()
            )
    except (FileNotFoundError, NotADirectoryError, PermissionError):
        return 0


@router.get("/overview", response_model=OverviewResponse)
async def overview(request: Request, _: CurrentAdmin, session: Session) -> OverviewResponse:
    since = datetime.now(UTC) - RECENT_WINDOW

    titles = await _count(session, select(func.count()).select_from(Media))
    added = await _count(
        session, select(func.count()).select_from(Media).where(Media.created_at >= since)
    )

    tagged_ids = select(MediaTag.media_id).distinct().scalar_subquery()
    tagged = await _count(
        session, select(func.count()).select_from(Media).where(Media.id.in_(tagged_ids))
    )

    matched = await _count(
        session,
        select(func.count()).select_from(Media).where(Media.confidence.is_not(None)),
    )

    duplicates = await _count(session, select(func.count()).select_from(QuarantineItem))

    guests = await _count(
        session, select(func.count()).select_from(User).where(User.role != UserRole.ADMIN)
    )

    folders = list(
        (await session.scalars(select(RootFolder).where(RootFolder.enabled.is_(True)))).all()
    )
    # `last_space_checked_at` is the measured signal, not the numbers. A folder
    # that has never been checked still carries a `free_space_bytes` of 0 —
    # reading that as "the disk is full" would be the worst possible way to be
    # wrong on a storage card. Pairing the two figures here also keeps "used"
    # and "total" over the same set of volumes.
    measured: list[tuple[int, int]] = []
    for folder in folders:
        if folder.last_space_checked_at is None or folder.total_space_bytes is None:
            continue
        measured.append((folder.total_space_bytes, folder.free_space_bytes))

    storage = StorageTotals(
        used_bytes=sum(total - free for total, free in measured) if measured else None,
        total_bytes=sum(total for total, _ in measured) if measured else None,
        volumes=len(folders),
    )

    scans = [folder.last_scanned_at for folder in folders if folder.last_scanned_at is not None]

    return OverviewResponse(
        titles=titles,
        titles_added_this_week=added,
        untagged=titles - tagged,
        guests=guests,
        storage=storage,
        health=LibraryHealth(
            total=titles,
            metadata_matched=matched,
            artwork_present=min(
                _artwork_present(request.app.state.settings.thumbnail_path), titles
            ),
            tagged=tagged,
            duplicates_flagged=duplicates,
        ),
        last_scan_at=max(scans) if scans else None,
    )
