"""Database secret checks used by backup and restore tooling."""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from pornarr_db.backup import APP_SECRET_CHECK_KEY, ensure_app_secret_matches
from pornarr_db.base import Base
from pornarr_db.models.settings import Setting
from pornarr_shared.errors import ConfigurationError


@pytest.fixture
async def session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    try:
        async with AsyncSession(engine, expire_on_commit=False) as database_session:
            yield database_session
    finally:
        await engine.dispose()


async def test_secret_check_is_encrypted_and_rejects_a_different_secret(
    session: AsyncSession,
) -> None:
    original_secret = "a" * 32

    await ensure_app_secret_matches(session, original_secret)
    await session.commit()

    stored = await session.get(Setting, APP_SECRET_CHECK_KEY)
    assert stored is not None
    assert stored.value != original_secret

    await ensure_app_secret_matches(session, original_secret)

    with pytest.raises(ConfigurationError, match="APP_SECRET does not match"):
        await ensure_app_secret_matches(session, "b" * 32)
