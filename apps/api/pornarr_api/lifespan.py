"""Startup and shutdown.

Connections are opened once at startup and closed on shutdown. Opening them
lazily per request looks simpler and produces a connection storm the first time
real traffic arrives.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import redis.asyncio as redis
from fastapi import FastAPI

from pornarr_db.session import dispose_engine, get_engine
from pornarr_shared.config import Settings
from pornarr_shared.logging import install_redaction, register_secret

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings: Settings = app.state.settings

    # Registered before anything else can log: the secret must never reach a log
    # line, and the first thing that could leak it is a connection error.
    register_secret(settings.app_secret.get_secret_value())
    install_redaction()

    app.state.redis = redis.from_url(settings.redis_url, decode_responses=True)
    app.state.engine = get_engine()

    logger.info("api started in %s mode", settings.app_env)
    try:
        yield
    finally:
        await app.state.redis.aclose()
        await dispose_engine()
        logger.info("api stopped")
