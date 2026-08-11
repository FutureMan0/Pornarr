"""Scheduled storage-refresh behaviour."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from pornarr_db.base import Base
from pornarr_db.models.root_folders import RootFolder
from pornarr_db.models.user import User, UserRole
from pornarr_worker.jobs.storage import refresh_root_folder_space


class RecordingRedis:
    def __init__(self) -> None:
        self.events: list[dict[str, str]] = []

    async def xadd(self, _: str, fields: dict[str, str]) -> str:
        self.events.append(fields)
        return f"{len(self.events)}-0"

    async def publish(self, _: str, __: str) -> int:
        return 1


@dataclass(frozen=True)
class DiskUsage:
    total: int
    used: int
    free: int


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


async def test_low_space_notifies_active_administrators_once(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    admin = User(username="root", password_hash="hash", role=UserRole.ADMIN)
    folder = RootFolder(path=str(tmp_path), free_space_bytes=0)
    session.add_all((admin, folder))
    await session.flush()
    redis = RecordingRedis()
    monkeypatch.setattr(
        "pornarr_worker.jobs.storage.shutil.disk_usage",
        lambda _: DiskUsage(total=100, used=88, free=12),
    )

    refreshed = await refresh_root_folder_space(session, redis, minimum_free_percent=10)

    assert refreshed == {"checked": 1, "unavailable": 0, "warnings": 1}
    assert folder.free_space_bytes == 12
    assert folder.total_space_bytes == 100
    assert folder.last_space_checked_at is not None
    assert folder.low_space_warning_sent is True
    assert redis.events == [
        {
            "type": "storage.low_space",
            "data": '{"path": "'
            + str(tmp_path)
            + '", "free_percent": 12, "threshold_percent": 15}',
            "user_id": str(admin.id),
        }
    ]

    assert await refresh_root_folder_space(session, redis, minimum_free_percent=10) == {
        "checked": 1,
        "unavailable": 0,
        "warnings": 0,
    }
    assert len(redis.events) == 1
