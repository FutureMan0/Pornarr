"""Download queue persistence invariants."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from pornarr_db.base import Base
from pornarr_db.downloads import is_release_blocked
from pornarr_db.models.download import BlockedRelease


async def test_expired_release_is_not_blocked() -> None:
    now = datetime.now(UTC)
    engine = create_async_engine("sqlite+aiosqlite://")
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)

        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        async with session_factory() as session:
            session.add_all(
                [
                    BlockedRelease(
                        release_guid="expired-release",
                        blocked_until=now - timedelta(seconds=1),
                    ),
                    BlockedRelease(
                        release_guid="active-release",
                        blocked_until=now + timedelta(minutes=1),
                    ),
                ]
            )
            await session.commit()

            assert not await is_release_blocked(session, "expired-release", now=now)
            assert await is_release_blocked(session, "active-release", now=now)
    finally:
        await engine.dispose()
