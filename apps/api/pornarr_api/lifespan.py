"""Startup and shutdown.

Connections are opened once at startup and closed on shutdown. Opening them
lazily per request looks simpler and produces a connection storm the first time
real traffic arrives.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress

import redis.asyncio as redis
from arq.connections import RedisSettings, create_pool
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession

from pornarr_db.backup import ensure_app_secret_matches
from pornarr_db.session import dispose_engine, get_engine
from pornarr_media.capabilities import detect_hardware_capabilities
from pornarr_media.sessions import TranscodeSessionRegistry
from pornarr_shared.config import Settings
from pornarr_shared.logging import configure_logging, install_redaction, register_secret

logger = logging.getLogger(__name__)
TRANSCODE_REAP_INTERVAL_SECONDS = 1


async def _reap_transcode_sessions(registry: TranscodeSessionRegistry) -> None:
    while True:
        await registry.reap_expired()
        await asyncio.sleep(TRANSCODE_REAP_INTERVAL_SECONDS)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings: Settings = app.state.settings

    # Registered before anything else can log: the secret must never reach a log
    # line, and the first thing that could leak it is a connection error.
    register_secret(settings.app_secret.get_secret_value())
    configure_logging(settings.log_level)
    install_redaction()

    app.state.engine = get_engine(settings)
    try:
        async with AsyncSession(app.state.engine) as session:
            await ensure_app_secret_matches(session, settings.app_secret.get_secret_value())
            await session.commit()
    except Exception:
        await dispose_engine()
        raise
    app.state.redis = redis.from_url(settings.redis_url, decode_responses=True)
    # ARQ speaks its own wire format and needs undecoded replies, so job
    # submission gets its own pool rather than borrowing the client above.
    app.state.job_queue = await create_pool(RedisSettings.from_dsn(settings.redis_url))
    app.state.hardware_capabilities = detect_hardware_capabilities(
        requested=settings.transcode_hwaccel
    )
    app.state.transcode_sessions = TranscodeSessionRegistry(
        app.state.redis, settings.transcode_path
    )
    reaper = asyncio.create_task(_reap_transcode_sessions(app.state.transcode_sessions))

    logger.info("api started in %s mode", settings.app_env)
    try:
        yield
    finally:
        reaper.cancel()
        with suppress(asyncio.CancelledError):
            await reaper
        # `aclose()` closes the client's own connection; the pool holds others.
        # Without disconnecting it, a restarting API leaves sockets for the
        # garbage collector, which shows up as a slow connection leak rather
        # than as an error.
        await app.state.redis.aclose()
        await app.state.redis.connection_pool.disconnect()
        await app.state.job_queue.aclose()
        await app.state.job_queue.connection_pool.disconnect()
        await dispose_engine()
        logger.info("api stopped")
