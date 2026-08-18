"""Turning stored provider keys into the adapters the cascade asks."""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from pornarr_db.base import Base
from pornarr_db.models.metadata_provider import MetadataProvider
from pornarr_db.types import set_cipher
from pornarr_shared.crypto import CredentialCipher
from pornarr_worker.metadata_providers import configured_providers

SECRET = "0123456789abcdef0123456789abcdef"


@pytest.fixture(autouse=True)
def _cipher() -> Iterator[None]:
    set_cipher(CredentialCipher(SECRET))
    yield
    set_cipher(None)


@pytest.fixture
async def session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    async with async_sessionmaker(engine, expire_on_commit=False)() as created:
        yield created
    await engine.dispose()


async def test_enabled_providers_become_adapters_in_priority_order(session: AsyncSession) -> None:
    session.add(MetadataProvider(implementation="tpdb", api_key="tpdb-key", priority=1))
    session.add(MetadataProvider(implementation="stashdb", api_key="stash-key", priority=0))
    session.add(MetadataProvider(implementation="future", api_key="key", priority=3))
    await session.flush()

    adapters = await configured_providers(session)

    # The unknown implementation is configuration for a later version, not a
    # reason to fail every import.
    assert [adapter.name for adapter in adapters] == ["stashdb", "tpdb"]


async def test_a_disabled_provider_is_not_asked(session: AsyncSession) -> None:
    session.add(MetadataProvider(implementation="stashdb", api_key="key", enabled=False))
    await session.flush()

    assert await configured_providers(session) == ()
