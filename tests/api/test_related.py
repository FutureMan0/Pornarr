"""What "related" means, as ranking rather than as a query that ran.

The interesting claims are all about ordering and exclusion: a shared performer
must outrank a shared studio, a single shared tag must not qualify at all, and a
neighbour the caller may not see must not appear even though the similarity
computation found it.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from pornarr_api.main import create_app
from pornarr_db.base import Base
from pornarr_db.models.entities import MediaPerformer, MediaTag, Performer, Tag
from pornarr_db.models.media import Media, MediaFile
from pornarr_db.models.settings import Setting
from pornarr_db.models.user import User
from tests.api.test_app import build_settings
from tests.api.test_auth import MemoryRedis, create_user, login


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


def _media(title: str, *, studio: str | None = None, owner: User | None = None) -> Media:
    return Media(
        title=title,
        normalized_title=title.lower(),
        studio=studio,
        owner_id=None if owner is None else owner.id,
    )


async def _seed(app, user: User) -> dict[str, Media]:
    """One subject and four neighbours, each related in exactly one way."""
    factory = async_sessionmaker(app.state.engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        subject = _media("Subject", studio="Northwind")
        by_performer = _media("By performer")
        by_studio = _media("By studio", studio="Northwind")
        by_two_tags = _media("By two tags")
        by_one_tag = _media("By one tag")
        stranger = _media("Stranger")

        for media in (subject, by_performer, by_studio, by_two_tags, by_one_tag, stranger):
            session.add(MediaFile(media=media, path=f"/data/{media.title}.mp4", size=1))
        await session.flush()

        ada = Performer(name="Ada", normalized_name="ada")
        red = Tag(name="red", normalized_name="red")
        blue = Tag(name="blue", normalized_name="blue")
        green = Tag(name="green", normalized_name="green")
        session.add_all([ada, red, blue, green])
        await session.flush()

        # The subject: one performer, three tags, a studio.
        session.add_all(
            [
                MediaPerformer(media_id=subject.id, performer_id=ada.id),
                MediaTag(media_id=subject.id, tag_id=red.id, confidence=1.0, source="test"),
                MediaTag(media_id=subject.id, tag_id=blue.id, confidence=1.0, source="test"),
                MediaTag(media_id=subject.id, tag_id=green.id, confidence=1.0, source="test"),
                # One shared performer: 3.0
                MediaPerformer(media_id=by_performer.id, performer_id=ada.id),
                # Two shared tags: 2.0
                MediaTag(media_id=by_two_tags.id, tag_id=red.id, confidence=1.0, source="test"),
                MediaTag(media_id=by_two_tags.id, tag_id=blue.id, confidence=1.0, source="test"),
                # One shared tag: 1.0, below the floor
                MediaTag(media_id=by_one_tag.id, tag_id=red.id, confidence=1.0, source="test"),
            ]
        )
        await session.commit()

    return {
        "subject": subject,
        "performer": by_performer,
        "studio": by_studio,
        "two_tags": by_two_tags,
        "one_tag": by_one_tag,
        "stranger": stranger,
    }


async def test_ranks_a_shared_performer_above_a_shared_studio(app, client: AsyncClient) -> None:
    user: User = await create_user(app)
    seeded = await _seed(app, user)
    await login(client, user.username, "correct horse battery staple")

    response = await client.get(f"/api/media/{seeded['subject'].id}/related")

    assert response.status_code == 200
    titles = [item["title"] for item in response.json()]
    # A performer holds dozens of titles and a studio holds thousands, so
    # sharing one is the stronger statement.
    assert titles.index("By performer") < titles.index("By two tags")
    assert titles.index("By two tags") < titles.index("By studio")


async def test_one_shared_tag_is_a_coincidence_not_a_relation(app, client: AsyncClient) -> None:
    user: User = await create_user(app)
    seeded = await _seed(app, user)
    await login(client, user.username, "correct horse battery staple")

    response = await client.get(f"/api/media/{seeded['subject'].id}/related")

    titles = [item["title"] for item in response.json()]
    # Almost every title shares one tag with almost every other; a row full of
    # those is worse than an empty row.
    assert "By one tag" not in titles
    assert "Stranger" not in titles


async def test_names_the_strongest_link_rather_than_all_of_them(app, client: AsyncClient) -> None:
    user: User = await create_user(app)
    seeded = await _seed(app, user)
    await login(client, user.username, "correct horse battery staple")

    response = await client.get(f"/api/media/{seeded['subject'].id}/related")

    by_title = {item["title"]: item for item in response.json()}
    assert by_title["By performer"]["reason"] == "performer"
    assert by_title["By studio"]["reason"] == "studio"
    assert by_title["By two tags"]["reason"] == "tag"


async def test_a_title_is_never_related_to_itself(app, client: AsyncClient) -> None:
    user: User = await create_user(app)
    seeded = await _seed(app, user)
    await login(client, user.username, "correct horse battery staple")

    response = await client.get(f"/api/media/{seeded['subject'].id}/related")

    assert all(item["media_id"] != str(seeded["subject"].id) for item in response.json())


async def test_the_limit_is_honoured(app, client: AsyncClient) -> None:
    user: User = await create_user(app)
    seeded = await _seed(app, user)
    await login(client, user.username, "correct horse battery staple")

    response = await client.get(f"/api/media/{seeded['subject'].id}/related?limit=1")

    body = response.json()
    assert len(body) == 1
    # The best one, not an arbitrary one: ranking happens before truncation.
    assert body[0]["title"] == "By performer"


async def test_an_unknown_title_is_a_404_not_an_empty_row(app, client: AsyncClient) -> None:
    user: User = await create_user(app)
    await login(client, user.username, "correct horse battery staple")

    response = await client.get("/api/media/00000000-0000-0000-0000-000000000000/related")

    assert response.status_code == 404


async def _set_flag(app, key: str, value: bool) -> None:
    async with AsyncSession(app.state.engine, expire_on_commit=False) as session:
        session.add(Setting(key=key, value=value))
        await session.commit()


async def test_a_neighbour_you_may_not_see_is_not_listed(app, client: AsyncClient) -> None:
    """Similarity must not become a way to enumerate someone else's titles.

    The computation finds the neighbour either way — it shares a performer with
    the subject. What must not happen is that it reaches the response.
    """
    alice: User = await create_user(app, username="alice")
    bob: User = await create_user(app, username="bob")
    await _set_flag(app, "private_libraries", True)

    factory = async_sessionmaker(app.state.engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        subject = _media("Subject", owner=alice)
        hers = _media("Hers", owner=alice)
        his = _media("His", owner=bob)
        for media in (subject, hers, his):
            session.add(MediaFile(media=media, path=f"/data/{media.title}.mp4", size=1))
        await session.flush()

        ada = Performer(name="Ada", normalized_name="ada")
        session.add(ada)
        await session.flush()
        session.add_all(
            [
                MediaPerformer(media_id=subject.id, performer_id=ada.id),
                MediaPerformer(media_id=hers.id, performer_id=ada.id),
                MediaPerformer(media_id=his.id, performer_id=ada.id),
            ]
        )
        await session.commit()

    await login(client, "alice", "correct horse battery staple")
    response = await client.get(f"/api/media/{subject.id}/related")

    titles = [item["title"] for item in response.json()]
    assert titles == ["Hers"]
