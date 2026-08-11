"""Scheduled free-space refresh and administrator warnings."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from pornarr_db.base import utcnow
from pornarr_db.models.root_folders import RootFolder
from pornarr_db.models.user import User, UserRole
from pornarr_db.session import session_scope
from pornarr_db.settings import get_runtime_settings
from pornarr_shared.config import get_settings
from pornarr_shared.events import publish_event
from pornarr_shared.jobs import job

WARNING_BUFFER_PERCENT = 5


async def refresh_root_folder_space(
    session: AsyncSession, redis: Any, *, minimum_free_percent: int
) -> dict[str, int]:
    """Measure every enabled root and notify administrators on a warning transition."""

    folders = await session.scalars(select(RootFolder).where(RootFolder.enabled.is_(True)))
    administrators = tuple(
        await session.scalars(
            select(User.id).where(User.role == UserRole.ADMIN, User.is_active.is_(True))
        )
    )
    warning_threshold = min(minimum_free_percent + WARNING_BUFFER_PERCENT, 100)
    result = {"checked": 0, "unavailable": 0, "warnings": 0}

    for folder in folders:
        try:
            usage = shutil.disk_usage(Path(folder.path))
        except OSError:
            result["unavailable"] += 1
            continue

        free_percent = usage.free * 100 // usage.total
        warning = free_percent <= warning_threshold
        folder.free_space_bytes = usage.free
        folder.last_space_checked_at = utcnow()
        result["checked"] += 1

        if warning and not folder.low_space_warning_sent:
            folder.low_space_warning_sent = True
            result["warnings"] += 1
            data = {
                "path": folder.path,
                "free_percent": free_percent,
                "threshold_percent": warning_threshold,
            }
            for administrator_id in administrators:
                await publish_event(redis, "storage.low_space", data, user_id=str(administrator_id))
        elif not warning:
            folder.low_space_warning_sent = False

    await session.flush()
    return result


async def refresh_storage(context: dict[str, Any]) -> dict[str, int]:
    """Refresh storage state hourly for health and automation decisions."""

    defaults = get_settings()
    async with session_scope() as session:
        runtime = await get_runtime_settings(session, defaults)
        return await refresh_root_folder_space(
            session,
            context["redis"],
            minimum_free_percent=runtime.min_free_disk_percent,
        )


REFRESH_STORAGE_JOB = job(refresh_storage)
