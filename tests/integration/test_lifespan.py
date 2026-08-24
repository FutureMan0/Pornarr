"""Application startup and shutdown against real services.

Lifespan is the one part of the application that cannot be tested without a
database and a Redis: its whole job is opening and closing those connections.
Mocking them would test the mock.

The lifespan context manager is driven directly rather than through Starlette's
`TestClient`, which is deprecated against httpx and would need a second HTTP
client stack just to start an application.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr

from pornarr_api.lifespan import lifespan
from pornarr_api.main import create_app
from pornarr_shared.config import Settings
from pornarr_shared.logging import RedactingFilter

pytestmark = pytest.mark.integration

SECRET = "0123456789abcdef0123456789abcdef"


def _settings(data_path: Path) -> Settings:
    database_url = os.environ.get("DATABASE_URL")
    redis_url = os.environ.get("REDIS_URL")
    if not database_url or not redis_url:
        pytest.fail("DATABASE_URL and REDIS_URL must point at real services.")
    # Lifespan calls `ensure_data_directories()`, so an unstated `data_path`
    # means the suite creates `/data` on whatever machine runs it. That worked
    # only where the directory happened to exist already, and failed on a
    # hosted runner, where it does not and the job user cannot create it.
    return Settings(
        app_secret=SecretStr(SECRET),
        database_url=database_url,
        redis_url=redis_url,
        app_env="test",
        data_path=data_path,
        backup_path=data_path / "backups",
    )


async def test_application_starts_and_stops_cleanly(tmp_path: Path) -> None:
    app = create_app(_settings(tmp_path))

    async with lifespan(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            assert (await client.get("/api/openapi.json")).status_code == 200


async def test_redis_is_connected_during_startup_and_the_pool_is_released_after(
    tmp_path: Path,
) -> None:
    app = create_app(_settings(tmp_path))

    async with lifespan(app):
        assert await app.state.redis.ping() is True

    # A redis client reconnects transparently after `aclose()`, so "does it
    # raise" is the wrong question. What matters is that no socket is still
    # open: `disconnect()` keeps the connection objects but must have closed
    # every one of them. One left connected per restart is a slow leak.
    pool = app.state.redis.connection_pool
    assert not any(connection.is_connected for connection in pool._available_connections)
    assert pool._in_use_connections == set()


async def test_database_engine_is_usable_during_startup(tmp_path: Path) -> None:
    from sqlalchemy import text

    app = create_app(_settings(tmp_path))

    async with lifespan(app), app.state.engine.connect() as connection:
        assert (await connection.execute(text("SELECT 1"))).scalar() == 1


async def test_startup_registers_the_secret_for_redaction(tmp_path: Path) -> None:
    """Registered before anything else can log: the first thing that could leak
    the secret is a connection error during startup."""
    RedactingFilter.extra_values.clear()
    app = create_app(_settings(tmp_path))

    async with lifespan(app):
        assert SECRET in RedactingFilter.extra_values

    record = logging.LogRecord("t", logging.INFO, __file__, 1, f"secret is {SECRET}", None, None)
    RedactingFilter().filter(record)
    assert SECRET not in record.msg


async def test_starting_twice_in_one_process_is_safe(tmp_path: Path) -> None:
    """The engine is process-wide and disposed on shutdown. A second application
    in the same process must get a working engine, not a disposed one."""
    from sqlalchemy import text

    for _ in range(2):
        app = create_app(_settings(tmp_path))
        async with lifespan(app), app.state.engine.connect() as connection:
            assert (await connection.execute(text("SELECT 1"))).scalar() == 1
