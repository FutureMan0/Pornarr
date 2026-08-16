"""Local library search behaviour."""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from pornarr_api.routers import search as search_router
from pornarr_db.media_search import MediaSearch, MediaSearchResult, MediaSort
from pornarr_db.models.filters import (
    ContentFilterProfile,
    ContentFilterRule,
    FilterAction,
    FilterProfileScope,
    FilterRuleKind,
)
from pornarr_db.models.indexer import Indexer
from pornarr_db.models.media import Media, MediaFile
from pornarr_db.models.playback import UserEvent
from pornarr_db.models.release import ReleaseCache
from pornarr_db.models.statistics import PerformanceMetric
from pornarr_db.statistics import record_measurement
from pornarr_db.types import set_cipher
from pornarr_shared.crypto import CredentialCipher
from pornarr_shared.jobs import INDEXER_QUEUE, indexer_search_state_key
from pornarr_shared.metrics import REGISTRY
from tests.api.test_app import SECRET
from tests.api.test_auth import create_user, csrf_headers, login

pytest_plugins = ["tests.api.test_auth"]


@pytest.fixture(autouse=True)
def _cipher() -> Iterator[None]:
    set_cipher(CredentialCipher(SECRET))
    yield
    set_cipher(None)


async def _cached_release(
    session: AsyncSession,
    indexer: Indexer,
    *,
    guid: str,
    title: str,
    size: int,
    published_at: datetime | None,
    seeders: int | None,
) -> ReleaseCache:
    release = ReleaseCache(
        indexer_id=indexer.id,
        guid=guid,
        title=title,
        normalized_title=title.casefold(),
        details_url=None,
        download_url=None,
        published_at=published_at,
        size=size,
        categories=[],
        seeders=seeders,
        peers=None,
        info_hash=None,
        magnet_url=None,
        groups=[],
        raw_payload={},
        expires_at=datetime.now(UTC) + timedelta(days=1),
    )
    session.add(release)
    await session.flush()
    return release


async def test_local_search_honours_the_requesting_users_filter_profile(
    app, client, monkeypatch: pytest.MonkeyPatch
) -> None:
    labels = {"operation": "search", "result": "ok"}
    before = REGISTRY.get_sample_value("pornarr_operations_total", labels=labels)
    assert before is not None
    user = await create_user(app)
    media = Media(
        id=uuid4(),
        title="Hidden Summer Scene",
        normalized_title="hidden summer scene",
    )
    media_file = MediaFile(
        id=uuid4(), media_id=media.id, path="/library/hidden.mp4", size=1_000, container="mp4"
    )

    async def fake_search_media(*_) -> list[MediaSearchResult]:
        return [MediaSearchResult(media=media, media_file=media_file, relevance=0.9)]

    monkeypatch.setattr(search_router, "search_media", fake_search_media)
    async with AsyncSession(app.state.engine) as session:
        profile = ContentFilterProfile(scope=FilterProfileScope.USER, user_id=user.id)
        profile.rules.append(
            ContentFilterRule(
                kind=FilterRuleKind.TERM,
                pattern="hidden",
                action=FilterAction.REJECT,
                enabled=True,
            )
        )
        session.add(profile)
        await session.commit()

    await login(client, user.username, "correct horse battery staple")

    response = await client.get("/api/search/local", params={"q": "summer"})

    assert response.status_code == 200
    assert response.json() == {"items": [], "next_cursor": None}
    assert REGISTRY.get_sample_value("pornarr_operations_total", labels=labels) == before + 1
    async with AsyncSession(app.state.engine) as session:
        event = await session.scalar(select(UserEvent))
    assert event is not None
    assert event.event_type == "search"
    assert event.value == 0


async def test_local_search_uses_the_shared_size_age_and_quality_filters(
    app, client, monkeypatch: pytest.MonkeyPatch
) -> None:
    user = await create_user(app)
    captured: MediaSearch | None = None

    async def fake_search_media(_, search: MediaSearch) -> list[MediaSearchResult]:
        nonlocal captured
        captured = search
        return []

    monkeypatch.setattr(search_router, "search_media", fake_search_media)
    await login(client, user.username, "correct horse battery staple")

    response = await client.get(
        "/api/search/local",
        params={
            "q": "Example",
            "quality": "1080p",
            "minimum_size_bytes": 1_000,
            "maximum_size_bytes": 2_000,
            "maximum_age_days": 30,
            "sort": "age",
        },
    )

    assert response.status_code == 200
    assert captured is not None
    assert captured.quality == "1080p"
    assert captured.minimum_size_bytes == 1_000
    assert captured.maximum_size_bytes == 2_000
    assert captured.maximum_age_days == 30
    assert captured.sort is MediaSort.AGE

    invalid = await client.get(
        "/api/search/local",
        params={"q": "Example", "minimum_size_bytes": 2_000, "maximum_size_bytes": 1_000},
    )

    assert invalid.status_code == 422


async def test_indexer_search_enqueues_a_user_scoped_job_and_publishes_its_start(
    app, client
) -> None:
    user = await create_user(app)
    calls: list[tuple[str, tuple[object, ...], dict[str, object]]] = []

    async def enqueue_job(function: str, *args: object, **kwargs: object) -> None:
        calls.append((function, args, kwargs))

    app.state.redis.enqueue_job = enqueue_job
    await login(client, user.username, "correct horse battery staple")

    response = await client.post(
        "/api/search/indexers",
        json={"q": "Example Scene"},
        headers=csrf_headers(client),
    )

    assert response.status_code == 202
    search_id = UUID(response.json()["id"])
    assert calls == [
        (
            "search_indexers",
            (str(search_id), str(user.id), "Example Scene"),
            {"_queue_name": INDEXER_QUEUE},
        )
    ]
    assert json.loads(app.state.redis.values[indexer_search_state_key(str(search_id))]) == {
        "id": str(search_id),
        "user_id": str(user.id),
        "query": "Example Scene",
        "statuses": {},
        "results": {},
        "cancelled": False,
    }
    assert app.state.redis.events[-1]["type"] == "search.started"
    assert app.state.redis.events[-1]["user_id"] == str(user.id)


async def test_indexer_search_status_filters_results_and_sorts_unknown_estimates_last(
    app, client
) -> None:
    user = await create_user(app)
    now = datetime.now(UTC)
    async with AsyncSession(app.state.engine) as session:
        torrent = Indexer(
            name="Torrent",
            protocol="torrent",
            implementation="torznab",
            base_url="https://torrent.example",
            api_key="secret",
        )
        usenet = Indexer(
            name="Usenet",
            protocol="usenet",
            implementation="newznab",
            base_url="https://usenet.example",
            api_key="secret",
        )
        session.add_all((torrent, usenet))
        await session.flush()
        fast = await _cached_release(
            session,
            torrent,
            guid="fast",
            title="Example Scene 1080p",
            size=2_000_000,
            published_at=now,
            seeders=10,
        )
        unknown = await _cached_release(
            session,
            torrent,
            guid="unknown",
            title="Example Scene 1080p",
            size=3_000_000,
            published_at=now,
            seeders=None,
        )
        old = await _cached_release(
            session,
            usenet,
            guid="old",
            title="Example Scene 1080p",
            size=2_000_000,
            published_at=now - timedelta(days=14),
            seeders=None,
        )
        existing = Media(id=uuid4(), title="Example Scene", normalized_title="example scene")
        session.add(existing)
        session.add(
            MediaFile(
                id=uuid4(),
                media_id=existing.id,
                path="/library/example.mp4",
                size=1_000_000,
                quality="720p",
            )
        )
        await record_measurement(
            session,
            metric=PerformanceMetric.DOWNLOAD_SPEED,
            scope="torrent",
            value=1_000_000,
        )
        await record_measurement(
            session,
            metric=PerformanceMetric.DOWNLOAD_SPEED,
            scope="usenet",
            value=500_000,
        )
        torrent_id = torrent.id
        usenet_id = usenet.id
        fast_id = fast.id
        existing_id = existing.id
        unknown_id = unknown.id
        old_id = old.id
        await session.commit()

    search_id = uuid4()
    app.state.redis.values[indexer_search_state_key(str(search_id))] = json.dumps(
        {
            "id": str(search_id),
            "user_id": str(user.id),
            "query": "Example Scene",
            "statuses": {str(torrent_id): "completed", str(usenet_id): "completed"},
            "results": {
                str(torrent_id): [{"guid": "fast"}, {"guid": "unknown"}],
                str(usenet_id): [{"guid": "old"}],
            },
            "cancelled": False,
        }
    )
    await login(client, user.username, "correct horse battery staple")

    filtered = await client.get(
        f"/api/search/indexers/{search_id}",
        params={
            "quality": "1080p",
            "minimum_size_bytes": 1_500_000,
            "maximum_size_bytes": 2_500_000,
            "maximum_age_days": 7,
            "indexer_id": str(torrent_id),
            "protocol": "torrent",
            "minimum_seeders": 1,
        },
    )

    assert filtered.status_code == 200
    assert [item["id"] for item in filtered.json()["items"]] == [str(fast_id)]
    assert filtered.json()["items"][0]["estimate"] == {
        "low_seconds": 1,
        "high_seconds": 2,
        "confidence": "low",
    }
    match = filtered.json()["items"][0]["match"]
    assert match["kind"] == "upgrade"
    assert match["media_id"] == str(existing_id)
    assert match["score"] > 0.5
    assert set(match["breakdown"]) == {"title", "attributes", "reliability"}

    sorted_response = await client.get(
        f"/api/search/indexers/{search_id}", params={"sort": "estimated_time"}
    )

    assert sorted_response.status_code == 200
    assert [item["id"] for item in sorted_response.json()["items"]] == [
        str(fast_id),
        str(old_id),
        str(unknown_id),
    ]


async def test_indexer_search_status_is_not_visible_to_another_user(app, client) -> None:
    owner = await create_user(app)
    viewer = await create_user(app, username="viewer")
    search_id = uuid4()
    app.state.redis.values[indexer_search_state_key(str(search_id))] = json.dumps(
        {
            "id": str(search_id),
            "user_id": str(owner.id),
            "query": "Example Scene",
            "statuses": {},
            "results": {},
            "cancelled": False,
        }
    )
    await login(client, viewer.username, "correct horse battery staple")

    response = await client.get(f"/api/search/indexers/{search_id}")

    assert response.status_code == 404
