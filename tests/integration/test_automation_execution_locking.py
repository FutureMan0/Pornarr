"""PostgreSQL row-lock guarantees for automatic requests."""

from __future__ import annotations

import asyncio
import os
import subprocess
from collections.abc import Iterator
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from pornarr_db.automation import AutomationCandidate, execute_automation
from pornarr_db.models.automation import AutomationRule
from pornarr_db.models.request import Request
from pornarr_db.models.root_folders import RootFolder
from pornarr_db.models.storage import DailyStorageUsage
from pornarr_db.models.user import User
from pornarr_db.settings import RuntimeSettings
from pornarr_shared.config import Settings

pytestmark = pytest.mark.integration

REPO_ROOT = Path(__file__).resolve().parents[2]


def _database_url() -> str:
    url = os.environ.get("DATABASE_URL")
    if not url:
        pytest.fail("DATABASE_URL is not set. Integration tests need a real database.")
    return url


def _alembic(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["uv", "run", "alembic", *args],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.fixture
def clean_database() -> Iterator[None]:
    with psycopg.connect(
        _database_url().replace("postgresql+psycopg://", "postgresql://"), autocommit=True
    ) as connection:
        connection.execute("DROP SCHEMA public CASCADE")
        connection.execute("CREATE SCHEMA public")
    yield


def _settings() -> RuntimeSettings:
    defaults = Settings(
        app_secret="0123456789abcdef0123456789abcdef",
        database_url=_database_url(),
        redis_url="redis://localhost:6379/15",
    )
    return RuntimeSettings.from_defaults(defaults, {"default_auto_downloads_enabled": True})


async def test_concurrent_automation_evaluations_reserve_only_one_last_slot(
    clean_database: None,
) -> None:
    assert _alembic("upgrade", "head").returncode == 0
    engine = create_async_engine(_database_url())
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    user_id = uuid4()
    try:
        async with sessions.begin() as session:
            session.add(User(id=user_id, username="automation-owner", password_hash="hash"))
            await session.flush()
            session.add_all(
                (
                    AutomationRule(
                        user_id=user_id,
                        enabled=True,
                        minimum_score=0,
                        daily_download_limit_gb=1,
                        max_concurrent_jobs=1,
                        max_downloads_per_day=1,
                    ),
                    RootFolder(
                        path="/library",
                        free_space_bytes=900_000_000,
                        total_space_bytes=1_000_000_000,
                    ),
                )
            )

        candidate = AutomationCandidate(
            user_id=user_id,
            release_guid="single-slot-release",
            query="Single slot",
            score=1,
            size_bytes=100_000_000,
            quality="1080p",
            metadata_confident=True,
            tags=(),
            performers=(),
            breakdown={},
        )

        async def attempt() -> bool:
            async with sessions.begin() as session:
                return (await execute_automation(session, candidate, _settings())).created

        assert sorted(await asyncio.gather(attempt(), attempt())) == [False, True]

        async with AsyncSession(engine) as session:
            assert len(list(await session.scalars(select(Request)))) == 1
            usage = await session.scalar(select(DailyStorageUsage))
            assert usage is not None
            assert usage.download_count == 0
            assert usage.reserved_download_count == 1
    finally:
        await engine.dispose()
