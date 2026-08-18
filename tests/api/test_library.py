from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from uuid import NAMESPACE_URL, uuid4, uuid5

import httpx
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from pornarr_api.main import create_app
from pornarr_db.base import Base
from pornarr_db.models.media import Media, MediaFile
from pornarr_db.models.peer import Peer
from pornarr_db.models.playback import PlaybackProgress
from pornarr_db.models.user import User
from pornarr_db.types import set_cipher
from pornarr_shared.crypto import CredentialCipher
from tests.api.test_app import SECRET, build_settings
from tests.api.test_auth import MemoryRedis, create_user, csrf_headers, login


@pytest.fixture(autouse=True)
def _cipher() -> Iterator[None]:
    """A peer's key is an encrypted column, so the suite needs the cipher."""
    set_cipher(CredentialCipher(SECRET))
    yield
    set_cipher(None)


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


async def test_library_browses_active_media_with_user_progress(app, client: AsyncClient) -> None:
    user: User = await create_user(app)
    factory = async_sessionmaker(app.state.engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        media = Media(title="Sample", normalized_title="sample")
        session.add(
            MediaFile(media=media, path="/data/library/sample.mp4", size=1, quality="1080p")
        )
        await session.flush()
        session.add(
            PlaybackProgress(
                user_id=user.id, media_id=media.id, position_seconds=10, duration_seconds=60
            )
        )
        await session.commit()
    assert (await client.get("/api/library")).status_code == 401
    await login(client, user.username, "correct horse battery staple")
    response = await client.get("/api/library", params={"limit": 1})
    assert response.status_code == 200
    assert response.json()["total"] == 1
    assert response.json()["items"] == [
        {
            "id": str(media.id),
            "title": "Sample",
            "studio": None,
            "release_date": None,
            "added_at": response.json()["items"][0]["added_at"],
            "duration_seconds": None,
            "quality": "1080p",
            "resolution": None,
            "position_seconds": 10,
            "progress_duration_seconds": 60,
            "completed": False,
            "poster_url": f"/api/media/{media.id}/poster",
            "sprite_url": f"/api/media/{media.id}/sprite",
            "rating": None,
            "rating_count": 0,
            "peer_id": None,
            "peer_name": None,
        }
    ]
    correction = await client.post(
        f"/api/media/{media.id}/tags", json={"name": "Verified"}, headers=csrf_headers(client)
    )
    assert correction.status_code == 201
    assert correction.json() == {"name": "Verified", "confidence": 1, "source": "manual"}
    detail = await client.get(f"/api/media/{media.id}")
    assert detail.status_code == 200
    assert detail.json()["tags"] == [correction.json()]
    assert detail.json()["path"] == "/data/library/sample.mp4"


async def test_library_filters_and_orders_by_what_the_screen_offers(app, client) -> None:
    """The facet list and the filters have to agree, or the screen offers dead ends."""

    from pornarr_db.models.entities import MediaTag, Tag

    user: User = await create_user(app)
    factory = async_sessionmaker(app.state.engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        older = Media(title="Alpha", normalized_title="alpha", studio="Probe Studio")
        newer = Media(title="Beta", normalized_title="beta", studio="Other Studio")
        session.add(MediaFile(media=older, path="/data/library/a.mp4", size=1, quality="1080p"))
        session.add(MediaFile(media=newer, path="/data/library/b.mp4", size=1, quality="720p"))
        tag = Tag(name="Solo", normalized_name="solo")
        session.add(tag)
        await session.flush()
        session.add(MediaTag(media_id=older.id, tag_id=tag.id, confidence=1, source="manual"))
        await session.commit()
    await login(client, user.username, "correct horse battery staple")

    facets = await client.get("/api/library/facets")
    assert facets.status_code == 200
    assert {facet["value"] for facet in facets.json()["studios"]} == {
        "Probe Studio",
        "Other Studio",
    }
    assert facets.json()["tags"] == [{"value": "Solo", "count": 1}]

    by_studio = await client.get("/api/library", params={"studio": "probe studio"})
    assert [item["title"] for item in by_studio.json()["items"]] == ["Alpha"]

    by_tag = await client.get("/api/library", params={"tag": "solo"})
    assert [item["title"] for item in by_tag.json()["items"]] == ["Alpha"]

    by_quality = await client.get("/api/library", params={"quality": "720p"})
    assert [item["title"] for item in by_quality.json()["items"]] == ["Beta"]

    by_title = await client.get("/api/library", params={"sort": "title"})
    assert [item["title"] for item in by_title.json()["items"]] == ["Alpha", "Beta"]


async def _peer(app, *, name: str = "friend") -> Peer:
    """A registered peer, as the administrator screen would have created it."""

    factory = async_sessionmaker(app.state.engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        peer = Peer(
            name=name, base_url="https://friend.example/api", api_key="pnr_remote", enabled=True
        )
        session.add(peer)
        await session.commit()
    return peer


def _remote_library(titles: list[str]):
    """A peer that pages its own library the way this instance pages its own."""

    def handler(request: httpx.Request) -> httpx.Response:
        # A peer must be asked for its own library only. Fanning out again is how
        # three friends turn one page request into an endless amplification loop.
        assert request.url.params["source"] == "local"
        offset = int(request.url.params["offset"])
        limit = int(request.url.params["limit"])
        window = sorted(titles)[offset : offset + limit]
        return httpx.Response(
            200,
            json={
                "total": len(titles),
                "next_offset": None,
                "items": [
                    {
                        "id": str(uuid5(NAMESPACE_URL, title)),
                        "title": title,
                        "studio": None,
                        "release_date": None,
                        "added_at": "2026-01-01T00:00:00+00:00",
                        "duration_seconds": None,
                        "quality": None,
                        "resolution": None,
                        "position_seconds": 42,
                        "progress_duration_seconds": 60,
                        "completed": True,
                        "poster_url": "https://tracker.example/pixel.gif",
                        "sprite_url": None,
                        "rating": None,
                        "rating_count": 0,
                        "peer_id": None,
                        "peer_name": None,
                    }
                    for title in window
                ],
            },
        )

    return handler


async def _two_local_titles(app) -> User:
    user: User = await create_user(app)
    factory = async_sessionmaker(app.state.engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        for title in ("Alpha", "Charlie"):
            media = Media(title=title, normalized_title=title.casefold())
            session.add(MediaFile(media=media, path=f"/data/library/{title}.mp4", size=1))
        await session.commit()
    return user


async def test_browsing_every_source_pages_a_merged_library_without_repeating_itself(
    app, client: AsyncClient
) -> None:
    """Two libraries, one grid: the pages concatenated must be the whole shelf."""

    user = await _two_local_titles(app)
    peer = await _peer(app)
    app.state.peer_transport = httpx.MockTransport(_remote_library(["Bravo", "Delta"]))
    await login(client, user.username, "correct horse battery staple")

    first = await client.get("/api/library", params={"source": "all", "sort": "title", "limit": 2})

    assert first.status_code == 200
    assert first.json()["total"] == 4
    assert [item["title"] for item in first.json()["items"]] == ["Alpha", "Bravo"]
    assert first.json()["next_offset"] is None
    assert first.json()["unavailable_peers"] == []
    remote = first.json()["items"][1]
    assert remote["peer_id"] == str(peer.id)
    assert remote["peer_name"] == "friend"
    # The peer's own URLs are ignored: it does not get to point this browser at
    # a third party, and it does not get to report somebody else's progress.
    assert remote["poster_url"] == f"/api/peers/{peer.id}/proxy/media/{remote['id']}/poster"
    assert remote["sprite_url"] == f"/api/peers/{peer.id}/proxy/media/{remote['id']}/sprite"
    assert remote["position_seconds"] is None
    assert remote["completed"] is False

    seen = list(first.json()["items"])
    cursor = first.json()["next_cursor"]
    while cursor:
        page = await client.get(
            "/api/library",
            params={"source": "all", "sort": "title", "limit": 2, "cursor": cursor},
        )
        seen.extend(page.json()["items"])
        cursor = page.json()["next_cursor"]

    assert [item["title"] for item in seen] == ["Alpha", "Bravo", "Charlie", "Delta"]


async def test_browsing_one_peer_reads_only_that_peer(app, client: AsyncClient) -> None:
    user = await _two_local_titles(app)
    peer = await _peer(app)
    app.state.peer_transport = httpx.MockTransport(_remote_library(["Bravo", "Delta"]))
    await login(client, user.username, "correct horse battery staple")

    page = await client.get(
        "/api/library", params={"source": str(peer.id), "sort": "title", "limit": 1}
    )

    assert [item["title"] for item in page.json()["items"]] == ["Bravo"]
    assert page.json()["total"] == 2
    # One source has one position, so plain offset paging still works — and it
    # has to agree with the cursor, or a client mixing the two sees Bravo twice.
    assert page.json()["next_offset"] == 1
    by_offset = await client.get(
        "/api/library", params={"source": str(peer.id), "sort": "title", "limit": 1, "offset": 1}
    )
    by_cursor = await client.get(
        "/api/library",
        params={
            "source": str(peer.id),
            "sort": "title",
            "limit": 1,
            "cursor": page.json()["next_cursor"],
        },
    )
    assert [item["title"] for item in by_offset.json()["items"]] == ["Delta"]
    assert by_cursor.json()["items"] == by_offset.json()["items"]


async def test_a_peer_that_is_down_does_not_take_down_the_browse(app, client: AsyncClient) -> None:
    """One unplugged household must cost that household's titles and nothing else."""

    user = await _two_local_titles(app)
    peer = await _peer(app)

    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route to host", request=request)

    app.state.peer_transport = httpx.MockTransport(refuse)
    await login(client, user.username, "correct horse battery staple")

    page = await client.get("/api/library", params={"source": "all", "sort": "title"})

    assert page.status_code == 200
    assert [item["title"] for item in page.json()["items"]] == ["Alpha", "Charlie"]
    assert page.json()["unavailable_peers"] == [{"id": str(peer.id), "name": "friend"}]
    # Its position is still in the cursor, so it rejoins the next page rather
    # than having been paged past while it was down.
    assert page.json()["next_cursor"] is not None


async def test_a_peer_that_answers_nonsense_is_not_merged(app, client: AsyncClient) -> None:
    """An item with no timestamp cannot be sorted, and a half-merged page pages wrong."""

    user = await _two_local_titles(app)
    await _peer(app)
    app.state.peer_transport = httpx.MockTransport(
        lambda _: httpx.Response(200, json={"items": [{"id": "not-a-uuid"}], "total": 9})
    )
    await login(client, user.username, "correct horse battery staple")

    page = await client.get("/api/library", params={"source": "all", "sort": "title"})

    assert [item["title"] for item in page.json()["items"]] == ["Alpha", "Charlie"]
    assert page.json()["total"] == 2
    assert [peer["name"] for peer in page.json()["unavailable_peers"]] == ["friend"]


async def test_a_cursor_cannot_be_replayed_against_another_sort(app, client: AsyncClient) -> None:
    user = await _two_local_titles(app)
    await _peer(app)
    app.state.peer_transport = httpx.MockTransport(_remote_library(["Bravo"]))
    await login(client, user.username, "correct horse battery staple")

    first = await client.get("/api/library", params={"source": "all", "sort": "title", "limit": 1})
    replayed = await client.get(
        "/api/library",
        params={"source": "all", "sort": "added", "cursor": first.json()["next_cursor"]},
    )

    assert replayed.status_code == 422
    assert (
        await client.get("/api/library", params={"source": "all", "cursor": "nonsense"})
    ).status_code == 422


async def test_an_unknown_source_is_not_browsable(app, client: AsyncClient) -> None:
    user = await _two_local_titles(app)
    await login(client, user.username, "correct horse battery staple")

    assert (await client.get("/api/library", params={"source": "elsewhere"})).status_code == 422
    assert (await client.get("/api/library", params={"source": str(uuid4())})).status_code == 404
