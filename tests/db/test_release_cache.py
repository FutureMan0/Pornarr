"""Expiry and idempotency of cached indexer releases."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from pornarr_db.base import Base
from pornarr_db.models.release import ReleaseCache
from pornarr_db.release_cache import ReleaseCacheRepository, normalize_release_title


def release(*, guid: str, title: str, expires_at: datetime) -> ReleaseCache:
    return ReleaseCache(
        indexer_id=uuid4(),
        guid=guid,
        title=title,
        normalized_title=normalize_release_title(title),
        categories=[],
        groups=[],
        raw_payload={"guid": guid, "title": title},
        expires_at=expires_at,
    )


async def test_cache_replaces_a_duplicate_and_excludes_expired_rows() -> None:
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    now = datetime.now(UTC)
    cached = release(guid="one", title="Example 1080p", expires_at=now + timedelta(hours=1))
    duplicate = release(guid="one", title="Changed title", expires_at=now + timedelta(hours=2))
    duplicate.indexer_id = cached.indexer_id
    expired = release(guid="two", title="Example expired", expires_at=now - timedelta(seconds=1))

    try:
        async with AsyncSession(engine, expire_on_commit=False) as session:
            repository = ReleaseCacheRepository(session)
            await repository.upsert(cached)
            await session.flush()
            await repository.upsert(duplicate)
            await repository.upsert(expired)
            await session.commit()

            found = await repository.search("changed", now=now)
            assert [item.guid for item in found] == ["one"]
            assert found[0].title == "Changed title"
            assert await repository.expire(now=now) == 1
            await session.commit()
            assert await repository.search("changed", now=now) == [found[0]]
    finally:
        await engine.dispose()


def test_title_normalization_ignores_case_punctuation_and_spacing() -> None:
    assert normalize_release_title("  Example---Release  ") == "example release"
