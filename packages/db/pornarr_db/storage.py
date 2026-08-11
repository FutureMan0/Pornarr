"""Storage accounting primitives shared by API and automation workers."""

from __future__ import annotations

from datetime import UTC, date, datetime
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from pornarr_db.models.storage import DailyStorageUsage


def utc_day(now: datetime | None = None) -> date:
    """Return the UTC calendar day that owns a budget counter."""

    return (now or datetime.now(UTC)).astimezone(UTC).date()


async def daily_storage_usage(
    session: AsyncSession, user_id: UUID, *, now: datetime | None = None
) -> DailyStorageUsage:
    """Read today's usage without creating a row for an unused budget."""

    day = utc_day(now)
    usage = await session.get(DailyStorageUsage, (user_id, day))
    return usage or DailyStorageUsage(
        user_id=user_id, day=day, downloaded_bytes=0, download_count=0
    )


async def record_daily_download(
    session: AsyncSession,
    user_id: UUID,
    downloaded_bytes: int,
    *,
    now: datetime | None = None,
) -> DailyStorageUsage:
    """Add one completed download to a user's current UTC-day usage."""

    if downloaded_bytes < 0:
        raise ValueError("downloaded_bytes cannot be negative")
    usage = await daily_storage_usage(session, user_id, now=now)
    if usage not in session:
        session.add(usage)
    usage.downloaded_bytes += downloaded_bytes
    usage.download_count += 1
    await session.flush()
    return usage
