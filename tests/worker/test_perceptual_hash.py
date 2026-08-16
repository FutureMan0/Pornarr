"""Background perceptual-hash candidate generation."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from pornarr_db.base import Base
from pornarr_db.models.media import DuplicateCandidate, Media, MediaFile
from pornarr_db.models.notification import Notification
from pornarr_db.models.user import User, UserRole
from pornarr_worker.jobs.phash import compare_perceptual_hash


async def test_close_hashes_create_one_non_destructive_candidate_and_notify_admin() -> None:
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        admin = User(username="admin", password_hash="hash", role=UserRole.ADMIN)
        first = MediaFile(
            media=Media(title="First", normalized_title="first"),
            path="/library/first.mkv",
            size=1,
            perceptual_hash="0000000000000000",
        )
        second = MediaFile(
            media=Media(title="Second", normalized_title="second"),
            path="/library/second.mkv",
            size=1,
            perceptual_hash="0000000000000003",
        )
        session.add_all([admin, first, second])
        await session.flush()

        candidates = await compare_perceptual_hash(session, RecordingRedis(), second)
        await session.commit()

        stored = list(await session.scalars(select(DuplicateCandidate)))
        notifications = list(await session.scalars(select(Notification)))
        assert candidates == 1
        assert len(stored) == 1
        assert (
            stored[0].media_file_id,
            stored[0].candidate_file_id,
            stored[0].hamming_distance,
        ) == (second.id, first.id, 2)
        assert len(notifications) == 1
    await engine.dispose()


class RecordingRedis:
    async def xadd(self, _: str, __: dict[str, object]) -> str:
        return "1-0"

    async def publish(self, _: str, __: str) -> None:
        return None
