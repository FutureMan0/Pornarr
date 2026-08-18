"""Startup and shutdown.

Connections are opened once at startup and closed on shutdown. Opening them
lazily per request looks simpler and produces a connection storm the first time
real traffic arrives.
"""

from __future__ import annotations

import asyncio
import logging
import signal
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager, suppress
from types import FrameType
from typing import Any

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


def _watch_for_shutdown(shutting_down: asyncio.Event) -> Callable[[], None]:
    """Set `shutting_down` the moment a termination signal arrives.

    WHY NOT THE LIFESPAN'S OWN SHUTDOWN. uvicorn closes its listening sockets,
    then *waits for open connections to finish*, and only then runs the lifespan
    shutdown. `/api/events` is a server-sent event stream that lives as long as
    the browser wants it, so a flag set on the way out is set after the thing it
    was meant to interrupt has already blocked — one idle tab was enough to make
    SIGTERM hang until the container's kill timeout, and to hang `--reload` on
    every code change in development.

    The signal is the only notice that arrives early enough. uvicorn installs its
    own handler before serving, so this chains rather than replaces: ours sets
    the event, then the previous handler runs and the server shuts down exactly
    as it would have.

    Returns a callable that puts the previous handlers back.
    """
    loop = asyncio.get_running_loop()
    previous: dict[signal.Signals, Any] = {}

    def install(number: signal.Signals) -> None:
        earlier = signal.getsignal(number)
        # `getsignal` also answers with SIG_DFL or SIG_IGN, which are ints and
        # not callables. Excluding int is what separates "a handler to chain to"
        # from "a disposition the operating system understands".
        chain = None if isinstance(earlier, int) or not callable(earlier) else earlier

        def handle(signum: int, frame: FrameType | None) -> None:
            # `call_soon_threadsafe`: a signal handler runs between bytecodes on
            # the main thread and must not touch the loop's internals directly.
            loop.call_soon_threadsafe(shutting_down.set)
            if chain is not None:
                chain(signum, frame)

        signal.signal(number, handle)
        previous[number] = earlier

    for number in (signal.SIGTERM, signal.SIGINT):
        try:
            install(number)
        except ValueError:
            # Not the main thread — a test harness, or an embedder running the
            # app inside a worker. The `finally` in the lifespan still covers it.
            logger.debug("no signal handler for %s: not the main thread", number.name)

    def restore() -> None:
        for number, earlier in previous.items():
            with suppress(ValueError):
                signal.signal(number, earlier)

    return restore


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
    # A second client, for the job queue.
    #
    # It cannot be the one above. That one decodes responses to `str`, which is
    # right for the session store and every other value the API reads back as
    # text; arq stores job payloads as packed bytes and a decoding client
    # corrupts them on the way out. `ArqRedis` is a `Redis` subclass, so the
    # temptation is to use one object for both — the encoding is what makes
    # that impossible, not the API surface.
    app.state.queue = await create_pool(RedisSettings.from_dsn(settings.redis_url))
    app.state.hardware_capabilities = detect_hardware_capabilities(
        requested=settings.transcode_hwaccel
    )
    app.state.transcode_sessions = TranscodeSessionRegistry(
        app.state.redis, settings.transcode_path
    )
    reaper = asyncio.create_task(_reap_transcode_sessions(app.state.transcode_sessions))

    app.state.shutting_down = asyncio.Event()
    restore_signals = _watch_for_shutdown(app.state.shutting_down)

    logger.info("api started in %s mode", settings.app_env)
    try:
        yield
    finally:
        # Belt and braces. The signal handler above is what fires in time; this
        # covers a shutdown that arrives some other way — a test harness exiting
        # the context manager, or an embedder driving the lifespan directly.
        app.state.shutting_down.set()
        restore_signals()
        reaper.cancel()
        with suppress(asyncio.CancelledError):
            await reaper
        # `aclose()` closes the client's own connection; the pool holds others.
        # Without disconnecting it, a restarting API leaves sockets for the
        # garbage collector, which shows up as a slow connection leak rather
        # than as an error.
        await app.state.redis.aclose()
        await app.state.redis.connection_pool.disconnect()
        await app.state.queue.aclose()
        await app.state.queue.connection_pool.disconnect()
        await dispose_engine()
        logger.info("api stopped")
