"""What the dashboard is allowed to claim.

Every number here ends up on a card an administrator reads at a glance, so the
tests are about honesty rather than plumbing: absent data must be absent and not
zero, a share must be a share of something stated, and a guest must not be able
to read any of it.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from pornarr_api.main import create_app
from pornarr_db.base import Base
from pornarr_db.models.entities import MediaTag, Tag
from pornarr_db.models.media import Media, MediaFile
from pornarr_db.models.root_folders import RootFolder
from pornarr_db.models.user import User, UserRole
from tests.api.test_app import build_settings
from tests.api.test_auth import MemoryRedis, create_user, login

PASSWORD = "correct horse battery staple"


@pytest.fixture
async def app() -> AsyncIterator:
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    application = create_app(build_settings())
    application.state.engine, application.state.redis = engine, MemoryRedis()
    yield application
    await engine.dispose()


@pytest.fixture
async def client(app) -> AsyncIterator[AsyncClient]:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as value:
        yield value


async def _seed(app, *, tagged: int, matched: int, total: int) -> None:
    factory = async_sessionmaker(app.state.engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        tag = Tag(name="night", normalized_name="night")
        session.add(tag)
        await session.flush()
        for index in range(total):
            media = Media(
                title=f"Title {index}",
                normalized_title=f"title {index}",
                confidence=0.8 if index < matched else None,
            )
            session.add(MediaFile(media=media, path=f"/data/{index}.mp4", size=1))
            await session.flush()
            if index < tagged:
                session.add(
                    MediaTag(media_id=media.id, tag_id=tag.id, confidence=1.0, source="test")
                )
        await session.commit()


async def _admin(app, client: AsyncClient) -> User:
    user = await create_user(app, username="root", role=UserRole.ADMIN)
    await login(client, "root", PASSWORD)
    return user


async def test_counts_the_library_and_what_is_missing_from_it(app, client: AsyncClient) -> None:
    await _admin(app, client)
    await _seed(app, tagged=3, matched=4, total=10)

    body = (await client.get("/api/admin/overview")).json()

    assert body["titles"] == 10
    assert body["health"]["tagged"] == 3
    # The card says "214 untagged", so it has to be the complement, not a
    # separate count that could disagree with the health bar beside it.
    assert body["untagged"] == 7
    assert body["health"]["metadata_matched"] == 4


async def test_a_share_travels_with_what_it_is_a_share_of(app, client: AsyncClient) -> None:
    await _admin(app, client)
    await _seed(app, tagged=1, matched=1, total=4)

    health = (await client.get("/api/admin/overview")).json()["health"]

    # "73% tagged" means nothing without the denominator, and computing the
    # percentage server-side would round it somewhere the client cannot see.
    assert health["total"] == 4
    assert health["tagged"] == 1


async def test_unmeasured_storage_is_absent_rather_than_zero(app, client: AsyncClient) -> None:
    await _admin(app, client)
    factory = async_sessionmaker(app.state.engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        # Never space-checked. The columns still hold their defaults.
        session.add(RootFolder(path="/data/library", enabled=True, free_space_bytes=0))
        await session.commit()

    storage = (await client.get("/api/admin/overview")).json()["storage"]

    # A confident "0 B of 0 B" reads as an empty disk. Nothing has measured it.
    assert storage["used_bytes"] is None
    assert storage["total_bytes"] is None
    assert storage["volumes"] == 1


async def test_storage_sums_only_the_volumes_that_reported(app, client: AsyncClient) -> None:
    await _admin(app, client)
    factory = async_sessionmaker(app.state.engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        session.add(
            RootFolder(
                path="/data/a",
                enabled=True,
                total_space_bytes=1_000,
                free_space_bytes=400,
                last_space_checked_at=datetime.now(UTC),
            )
        )
        # Never checked: its zeroes must not drag the ratio down.
        session.add(RootFolder(path="/data/b", enabled=True, free_space_bytes=0))
        await session.commit()

    storage = (await client.get("/api/admin/overview")).json()["storage"]

    # Used and total come from the same set of volumes, so the ratio is real.
    assert storage["used_bytes"] == 600
    assert storage["total_bytes"] == 1_000
    assert storage["volumes"] == 2


async def test_this_week_means_this_week(app, client: AsyncClient) -> None:
    await _admin(app, client)
    factory = async_sessionmaker(app.state.engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        recent = Media(title="New", normalized_title="new")
        old = Media(title="Old", normalized_title="old")
        session.add_all([recent, old])
        await session.flush()
        old.created_at = datetime.now(UTC) - timedelta(days=30)
        await session.commit()

    body = (await client.get("/api/admin/overview")).json()

    assert body["titles"] == 2
    assert body["titles_added_this_week"] == 1


async def test_an_empty_library_answers_zero_rather_than_failing(app, client: AsyncClient) -> None:
    await _admin(app, client)

    response = await client.get("/api/admin/overview")

    assert response.status_code == 200
    body = response.json()
    # A fresh server is the first thing anyone sees. Dividing by a zero total is
    # the client's problem to avoid, but only if the server gets this far.
    assert body["titles"] == 0
    assert body["health"]["total"] == 0
    assert body["last_scan_at"] is None


async def test_a_guest_cannot_read_the_dashboard(app, client: AsyncClient) -> None:
    await create_user(app, username="mira", role=UserRole.USER)
    await login(client, "mira", PASSWORD)

    response = await client.get("/api/admin/overview")

    # It counts guests, names volumes and totals the disk. None of that is a
    # guest's business.
    assert response.status_code == 403
