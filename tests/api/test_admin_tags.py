"""Cleaning up a tag list.

Merge carries the only genuinely tricky case: a title that already has both
tags. A naive repoint collides with the uniqueness of (media, tag, source) and
takes the whole merge down, so that case is what most of this file is about.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from pornarr_api.main import create_app
from pornarr_db.base import Base
from pornarr_db.models.entities import MediaTag, Tag
from pornarr_db.models.media import Media
from pornarr_db.models.user import User, UserRole
from tests.api.test_app import build_settings
from tests.api.test_auth import MemoryRedis, create_user, csrf_headers, login

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


def factory(app):
    return async_sessionmaker(app.state.engine, class_=AsyncSession, expire_on_commit=False)


async def _admin(app, client: AsyncClient) -> User:
    user = await create_user(app, username="root", role=UserRole.ADMIN)
    await login(client, "root", PASSWORD)
    return user


async def _tag(app, name: str) -> Tag:
    async with factory(app)() as session:
        tag = Tag(name=name, normalized_name=name.casefold())
        session.add(tag)
        await session.commit()
        return tag


async def _media(app, title: str) -> Media:
    async with factory(app)() as session:
        media = Media(title=title, normalized_title=title.casefold())
        session.add(media)
        await session.commit()
        return media


async def _assign(app, media: Media, tag: Tag, source: str = "scan") -> None:
    async with factory(app)() as session:
        session.add(MediaTag(media_id=media.id, tag_id=tag.id, confidence=1.0, source=source))
        await session.commit()


async def test_the_list_is_ordered_by_use_not_by_name(app, client: AsyncClient) -> None:
    await _admin(app, client)
    popular = await _tag(app, "zzz-popular")
    await _tag(app, "aaa-unused")
    for index in range(3):
        await _assign(app, await _media(app, f"Title {index}"), popular)

    body = (await client.get("/api/admin/tags")).json()

    # The reason to open this screen is the long tail; sorting by name buries it
    # among the tags that are working fine.
    assert [tag["name"] for tag in body] == ["zzz-popular", "aaa-unused"]
    assert body[0]["media_count"] == 3
    assert body[1]["media_count"] == 0


async def test_an_unused_tag_reports_zero_rather_than_being_omitted(
    app, client: AsyncClient
) -> None:
    await _admin(app, client)
    await _tag(app, "orphan")

    body = (await client.get("/api/admin/tags")).json()

    # A tag on nothing is the first candidate for deletion. Hiding it is how a
    # tag list becomes unmaintainable.
    assert body[0]["media_count"] == 0
    assert body[0]["last_used_at"] is None


async def test_renaming_changes_the_name_and_what_uniqueness_is_decided_on(
    app, client: AsyncClient
) -> None:
    await _admin(app, client)
    tag = await _tag(app, "lowlight")

    response = await client.patch(
        f"/api/admin/tags/{tag.id}", json={"name": "Low Light"}, headers=csrf_headers(client)
    )

    assert response.status_code == 200
    assert response.json()["name"] == "Low Light"
    async with factory(app)() as session:
        stored = await session.get(Tag, tag.id)
        assert stored is not None
        # The importers match on the normalized form; leaving it stale would let
        # the next scan recreate the tag under its old spelling.
        assert stored.normalized_name == "low light"


async def test_renaming_onto_an_existing_tag_is_refused_rather_than_merged(
    app, client: AsyncClient
) -> None:
    await _admin(app, client)
    await _tag(app, "night")
    other = await _tag(app, "nite")

    response = await client.patch(
        f"/api/admin/tags/{other.id}", json={"name": "Night"}, headers=csrf_headers(client)
    )

    # Merging is a separate, deliberate action. Doing it on a typo would fold
    # two tags together with no way back.
    assert response.status_code == 409


async def test_merging_moves_assignments_and_removes_the_source(app, client: AsyncClient) -> None:
    await _admin(app, client)
    target = await _tag(app, "low-light")
    source = await _tag(app, "lowlight")
    media = await _media(app, "Aurora")
    await _assign(app, media, source)

    response = await client.post(
        f"/api/admin/tags/{target.id}/merge",
        json={"source_ids": [str(source.id)]},
        headers=csrf_headers(client),
    )

    assert response.status_code == 200
    assert response.json()["media_count"] == 1
    async with factory(app)() as session:
        assert await session.get(Tag, source.id) is None
        assignments = list((await session.scalars(select(MediaTag))).all())
        assert [a.tag_id for a in assignments] == [target.id]


async def test_a_title_carrying_both_tags_survives_the_merge(app, client: AsyncClient) -> None:
    """The case a naive repoint breaks on."""
    await _admin(app, client)
    target = await _tag(app, "night")
    source = await _tag(app, "nite")
    media = await _media(app, "Aurora")
    await _assign(app, media, target)
    await _assign(app, media, source)

    response = await client.post(
        f"/api/admin/tags/{target.id}/merge",
        json={"source_ids": [str(source.id)]},
        headers=csrf_headers(client),
    )

    assert response.status_code == 200
    # One assignment, not two and not a unique-constraint failure that aborts
    # the whole merge.
    assert response.json()["media_count"] == 1
    async with factory(app)() as session:
        assignments = list((await session.scalars(select(MediaTag))).all())
        assert len(assignments) == 1
        assert assignments[0].tag_id == target.id


async def test_the_same_tag_from_two_sources_is_kept_apart(app, client: AsyncClient) -> None:
    await _admin(app, client)
    target = await _tag(app, "night")
    source = await _tag(app, "nite")
    media = await _media(app, "Aurora")
    await _assign(app, media, target, source="scan")
    await _assign(app, media, source, source="scraper")

    await client.post(
        f"/api/admin/tags/{target.id}/merge",
        json={"source_ids": [str(source.id)]},
        headers=csrf_headers(client),
    )

    async with factory(app)() as session:
        assignments = list((await session.scalars(select(MediaTag))).all())
        # Uniqueness is on (media, tag, source), so both survive — which is the
        # point: the scan and the scraper each said so independently.
        assert sorted(a.source for a in assignments) == ["scan", "scraper"]


async def test_a_tag_cannot_be_merged_into_itself(app, client: AsyncClient) -> None:
    await _admin(app, client)
    tag = await _tag(app, "night")

    response = await client.post(
        f"/api/admin/tags/{tag.id}/merge",
        json={"source_ids": [str(tag.id)]},
        headers=csrf_headers(client),
    )

    assert response.status_code == 422


async def test_deleting_takes_its_assignments_with_it(app, client: AsyncClient) -> None:
    await _admin(app, client)
    tag = await _tag(app, "night")
    await _assign(app, await _media(app, "Aurora"), tag)

    response = await client.delete(f"/api/admin/tags/{tag.id}", headers=csrf_headers(client))

    assert response.status_code == 204
    async with factory(app)() as session:
        # No title left pointing at a tag that no longer exists.
        assert list((await session.scalars(select(MediaTag))).all()) == []


async def test_a_guest_cannot_reshape_the_library_vocabulary(app, client: AsyncClient) -> None:
    tag = await _tag(app, "night")
    await create_user(app, username="mira", role=UserRole.USER)
    await login(client, "mira", PASSWORD)

    listed = await client.get("/api/admin/tags")
    deleted = await client.delete(f"/api/admin/tags/{tag.id}", headers=csrf_headers(client))

    assert listed.status_code == 403
    assert deleted.status_code == 403
