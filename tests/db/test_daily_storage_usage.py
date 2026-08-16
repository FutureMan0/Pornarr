"""Daily user storage-accounting behaviour."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from pornarr_db.base import Base
from pornarr_db.models.user import User
from pornarr_db.storage import daily_storage_usage, record_daily_download


@pytest.fixture
async def session():
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    try:
        async with AsyncSession(engine, expire_on_commit=False) as database_session:
            yield database_session
    finally:
        await engine.dispose()


async def test_daily_storage_usage_accumulates_and_resets_at_utc_midnight(
    session: AsyncSession,
) -> None:
    user = User(username="alice", password_hash="hash")
    session.add(user)
    await session.flush()
    today = datetime(2026, 8, 10, 23, 59, tzinfo=UTC)

    await record_daily_download(session, user.id, 400, now=today)
    usage = await record_daily_download(session, user.id, 600, now=today)

    assert usage.downloaded_bytes == 1000
    assert usage.download_count == 2

    tomorrow = await daily_storage_usage(session, user.id, now=today + timedelta(minutes=1))

    assert tomorrow.downloaded_bytes == 0
    assert tomorrow.download_count == 0


async def test_daily_storage_usage_is_isolated_per_user(session: AsyncSession) -> None:
    first = User(id=uuid4(), username="alice", password_hash="hash")
    second = User(id=uuid4(), username="bob", password_hash="hash")
    session.add_all((first, second))
    await session.flush()

    await record_daily_download(session, first.id, 100)

    assert (await daily_storage_usage(session, first.id)).downloaded_bytes == 100
    assert (await daily_storage_usage(session, second.id)).downloaded_bytes == 0
