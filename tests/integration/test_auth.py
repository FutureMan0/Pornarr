"""Authentication against the PostgreSQL and Redis services used in production."""

from __future__ import annotations

import os
import subprocess
from collections.abc import AsyncIterator
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr
from sqlalchemy.ext.asyncio import AsyncSession

from pornarr_api.auth import CSRF_COOKIE, CSRF_HEADER, hash_password
from pornarr_api.lifespan import lifespan
from pornarr_api.main import create_app
from pornarr_db.models.user import User
from pornarr_shared.config import Settings

pytestmark = pytest.mark.integration

REPO_ROOT = Path(__file__).resolve().parents[2]
SECRET = "0123456789abcdef0123456789abcdef"


def settings(data_path: Path) -> Settings:
    database_url = os.environ.get("DATABASE_URL")
    redis_url = os.environ.get("REDIS_URL")
    if not database_url or not redis_url:
        pytest.fail("DATABASE_URL and REDIS_URL must point at real services.")
    # `session_cookie_secure` is stated for the reason
    # `tests/api/test_app.build_settings` gives: this drives the app over
    # `http://test`, a cookie jar does not return a `Secure` cookie to a
    # plain-HTTP request, and left to its default the sign-in here held only on
    # a machine whose `.env` sets `SESSION_COOKIE_SECURE=false`.
    return Settings(
        app_secret=SecretStr(SECRET),
        database_url=database_url,
        redis_url=redis_url,
        app_env="test",
        session_cookie_secure=False,
        # Lifespan creates the data tree, and without a path of its own that
        # is `/data` on the machine running the suite — which a hosted runner
        # neither has nor lets the job user create.
        data_path=data_path,
        backup_path=data_path / "backups",
    )


def upgrade_database() -> None:
    result = subprocess.run(
        ["uv", "run", "alembic", "upgrade", "head"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


@pytest.fixture
async def app(tmp_path: Path) -> AsyncIterator[FastAPI]:
    upgrade_database()
    application = create_app(settings(tmp_path))
    async with lifespan(application):
        yield application


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[AsyncClient]:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http_client:
        yield http_client


async def test_login_uses_postgres_for_users_and_redis_for_the_session(
    app: FastAPI, client: AsyncClient
) -> None:
    username = f"user-{uuid4().hex}"
    async with AsyncSession(app.state.engine) as session:
        session.add(
            User(username=username, password_hash=hash_password("correct horse battery staple"))
        )
        await session.commit()

    logged_in = await client.post(
        "/api/auth/login",
        json={"username": username, "password": "correct horse battery staple"},
    )

    assert logged_in.status_code == 200
    assert (await client.get("/api/auth/me")).status_code == 200
    csrf_token = client.cookies.get(CSRF_COOKIE)
    assert csrf_token is not None
    assert (
        await client.post("/api/auth/logout", headers={CSRF_HEADER: csrf_token})
    ).status_code == 204
