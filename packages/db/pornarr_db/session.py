"""Engine and session management.

One engine per process, created lazily and disposed on shutdown. Defined here
and nowhere else: a second engine means a second connection pool, and two pools
sized for the same database is how a worker exhausts PostgreSQL's connection
limit under load.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from pornarr_shared.config import Settings, get_settings

_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def get_engine(settings: Settings | None = None) -> AsyncEngine:
    global _engine
    if _engine is None:
        resolved_settings = settings or get_settings()
        _engine = create_async_engine(
            resolved_settings.database_url,
            pool_size=5,
            max_overflow=10,
            pool_pre_ping=True,
            # SQL logging would print bound parameters, which for the credential
            # tables means secrets. Never enabled by configuration.
            echo=False,
        )
    return _engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    global _session_factory
    if _session_factory is None:
        _session_factory = async_sessionmaker(
            bind=get_engine(),
            expire_on_commit=False,
            autoflush=False,
        )
    return _session_factory


@asynccontextmanager
async def session_scope() -> AsyncIterator[AsyncSession]:
    """A session that commits on success and rolls back on any exception.

    Jobs must be idempotent, which they cannot be if a failure leaves half a unit
    of work committed.
    """
    async with get_session_factory()() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def dispose_engine() -> None:
    """Close the pool. Called from application shutdown."""
    global _engine, _session_factory
    if _engine is not None:
        await _engine.dispose()
    _engine = None
    _session_factory = None
