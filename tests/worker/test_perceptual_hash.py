"""Background perceptual-hash candidate generation."""

from __future__ import annotations

import pytest
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


# --- The Hamming threshold (docs/pipelines/import.md, "Afterwards") -----------
#
# "a Hamming distance of eight or less records a duplicate candidate. It never
# deletes anything; it raises an administrator notice." Two rows on each side of
# eight, and one that proves nothing is removed when a match is found.


async def _library_with(first_hash: str, second_hash: str):
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    session = factory()
    session.add(User(username="admin", password_hash="hash", role=UserRole.ADMIN))
    existing = MediaFile(
        media=Media(title="Existing", normalized_title="existing"),
        path="/library/existing.mkv",
        size=1,
        perceptual_hash=first_hash,
    )
    arriving = MediaFile(
        media=Media(title="Arriving", normalized_title="arriving"),
        path="/library/arriving.mkv",
        size=1,
        perceptual_hash=second_hash,
    )
    session.add_all([existing, arriving])
    await session.flush()
    return engine, session, existing, arriving


@pytest.mark.parametrize(
    ("what", "other_hash", "distance", "recorded"),
    [
        ("an identical hash", "0000000000000000", 0, True),
        ("one bit apart", "0000000000000001", 1, True),
        ("seven bits apart", "000000000000007f", 7, True),
        ("exactly eight bits apart, which is 'eight or less'", "00000000000000ff", 8, True),
        ("nine bits apart, which is not", "00000000000001ff", 9, False),
        ("nothing in common at all", "ffffffffffffffff", 64, False),
    ],
)
async def test_the_documented_hamming_threshold(
    what: str, other_hash: str, distance: int, recorded: bool
) -> None:
    engine, session, existing, arriving = await _library_with("0000000000000000", other_hash)
    async with session:
        matches = await compare_perceptual_hash(session, RecordingRedis(), arriving)
        await session.commit()

        candidates = list(await session.scalars(select(DuplicateCandidate)))
        assert (matches == 1) is recorded, what
        assert (len(candidates) == 1) is recorded, what
        if recorded:
            assert candidates[0].hamming_distance == distance, what
        # Whatever it decided, both files are still there. The job is advisory.
        assert len(list(await session.scalars(select(MediaFile)))) == 2
        assert {file.path for file in await session.scalars(select(MediaFile))} == {
            existing.path,
            arriving.path,
        }
    await engine.dispose()


async def test_a_second_run_over_the_same_pair_adds_neither_a_candidate_nor_a_notice() -> None:
    """The nightly job sweeps the whole library, so it meets every pair again."""
    engine, session, _, arriving = await _library_with("0000000000000000", "0000000000000003")
    async with session:
        first = await compare_perceptual_hash(session, RecordingRedis(), arriving)
        second = await compare_perceptual_hash(session, RecordingRedis(), arriving)
        await session.commit()

        assert (first, second) == (1, 0)
        assert len(list(await session.scalars(select(DuplicateCandidate)))) == 1
        assert len(list(await session.scalars(select(Notification)))) == 1
    await engine.dispose()
