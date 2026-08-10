"""Queries over persistent download work."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from pornarr_db.base import utcnow
from pornarr_db.models.download import BlockedRelease


async def is_release_blocked(
    session: AsyncSession, release_guid: str, *, now: datetime | None = None
) -> bool:
    """Return whether a release has a block that has not yet expired."""
    return (
        await session.scalar(
            select(BlockedRelease.id)
            .where(
                BlockedRelease.release_guid == release_guid,
                BlockedRelease.blocked_until > (now or utcnow()),
            )
            .limit(1)
        )
        is not None
    )
