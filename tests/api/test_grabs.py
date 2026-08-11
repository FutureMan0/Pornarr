"""Release grabbing rejects unsafe work before touching a client."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from pornarr_db.models.download import BlockedRelease, DownloadJob
from pornarr_db.models.download_client import DownloadClient
from pornarr_db.models.filters import (
    ContentFilterProfile,
    ContentFilterRule,
    FilterAction,
    FilterProfileScope,
    FilterRuleKind,
)
from pornarr_db.models.indexer import Indexer, IndexerStats
from pornarr_db.models.media import Media
from pornarr_db.models.release import ReleaseCache
from pornarr_db.types import set_cipher
from pornarr_shared.crypto import CredentialCipher
from tests.api.test_app import SECRET
from tests.api.test_auth import create_user, csrf_headers, login

pytest_plugins = ("tests.api.test_auth",)


@pytest.fixture(autouse=True)
def _cipher() -> Iterator[None]:
    set_cipher(CredentialCipher(SECRET))
    yield
    set_cipher(None)


class RecordingTorrentAdapter:
    def __init__(self) -> None:
        self.magnets: list[str] = []

    async def add_magnet(
        self,
        *,
        host: str,
        port: int,
        url_base: str,
        credentials: str,
        magnet: str,
        category: str | None,
        paused: bool,
    ) -> None:
        assert (host, port, url_base, credentials, category, paused) == (
            "client.example",
            8080,
            "",
            "secret",
            "pornarr",
            False,
        )
        self.magnets.append(magnet)


async def _release(
    session: AsyncSession,
    *,
    guid: str = "release-1",
    title: str = "Example",
    expires_at: datetime | None = None,
) -> ReleaseCache:
    indexer = Indexer(
        name=f"Indexer {guid}",
        protocol="torrent",
        implementation="torznab",
        base_url="https://indexer.example",
        api_key="secret",
    )
    session.add(indexer)
    await session.flush()
    session.add(IndexerStats(indexer_id=indexer.id))
    release = ReleaseCache(
        indexer_id=indexer.id,
        guid=guid,
        title=title,
        normalized_title=title.casefold(),
        details_url=None,
        download_url=None,
        published_at=None,
        size=100,
        categories=[],
        info_hash="0123456789abcdef0123456789abcdef01234567",
        magnet_url="magnet:?xt=urn:btih:0123456789abcdef0123456789abcdef01234567",
        groups=[],
        raw_payload={},
        expires_at=expires_at or datetime.now(UTC) + timedelta(days=1),
    )
    session.add(release)
    await session.flush()
    return release


async def _torrent_client(session: AsyncSession) -> DownloadClient:
    client = DownloadClient(
        name="Torrent",
        protocol="torrent",
        implementation="qbittorrent",
        host="client.example",
        port=8080,
        credentials="secret",
        category="pornarr",
        health="healthy",
    )
    session.add(client)
    await session.flush()
    return client


async def _request(client, *, query: str = "Example") -> str:
    response = await client.post(
        "/api/requests", json={"query": query}, headers=csrf_headers(client)
    )
    assert response.status_code == 201
    return response.json()["id"]


async def test_grab_submits_once_and_reuses_the_same_job_for_a_double_click(app, client) -> None:
    user = await create_user(app)
    adapter = RecordingTorrentAdapter()
    app.state.download_client_adapters = {"qbittorrent": adapter}
    async with AsyncSession(app.state.engine, expire_on_commit=False) as session:
        release = await _release(session)
        await _torrent_client(session)
        await session.commit()
    await login(client, user.username, "correct horse battery staple")
    request_id = await _request(client)

    first = await client.post(
        f"/api/requests/{request_id}/grab",
        json={"release_id": str(release.id)},
        headers=csrf_headers(client),
    )
    second = await client.post(
        f"/api/requests/{request_id}/grab",
        json={"release_id": str(release.id)},
        headers=csrf_headers(client),
    )

    assert first.status_code == 201
    assert second.status_code == 200
    assert second.json() == first.json()
    assert adapter.magnets == [release.magnet_url]
    async with AsyncSession(app.state.engine, expire_on_commit=False) as session:
        assert await session.scalar(select(func.count()).select_from(DownloadJob)) == 1
        job = await session.scalar(select(DownloadJob))
    assert job is not None
    assert job.release_guid == release.guid
    assert job.client_job_id == release.info_hash


async def test_expired_release_is_refused_before_client_routing(app, client) -> None:
    user = await create_user(app)
    async with AsyncSession(app.state.engine, expire_on_commit=False) as session:
        release = await _release(session, expires_at=datetime.now(UTC) - timedelta(seconds=1))
        await session.commit()
    await login(client, user.username, "correct horse battery staple")
    request_id = await _request(client)

    response = await client.post(
        f"/api/requests/{request_id}/grab",
        json={"release_id": str(release.id)},
        headers=csrf_headers(client),
    )

    assert response.status_code == 409
    assert response.json()["code"] == "RELEASE_EXPIRED"


async def test_missing_release_has_a_structured_refusal(app, client) -> None:
    user = await create_user(app)
    await login(client, user.username, "correct horse battery staple")
    request_id = await _request(client)

    response = await client.post(
        f"/api/requests/{request_id}/grab",
        json={"release_id": str(uuid4())},
        headers=csrf_headers(client),
    )

    assert response.status_code == 404
    assert response.json()["code"] == "RELEASE_NOT_FOUND"


async def test_blocked_library_and_filtered_releases_have_distinct_refusals(app, client) -> None:
    user = await create_user(app)
    async with AsyncSession(app.state.engine, expire_on_commit=False) as session:
        blocked = await _release(session, guid="blocked", title="Blocked")
        library = await _release(session, guid="library", title="In Library")
        filtered = await _release(session, guid="filtered", title="Filtered")
        session.add(
            BlockedRelease(
                release_guid=blocked.guid, blocked_until=datetime.now(UTC) + timedelta(days=1)
            )
        )
        session.add(Media(title="In Library", normalized_title="in library"))
        profile = ContentFilterProfile(scope=FilterProfileScope.USER, user_id=user.id)
        profile.rules.append(
            ContentFilterRule(
                kind=FilterRuleKind.TERM,
                pattern="filtered",
                action=FilterAction.REJECT,
                enabled=True,
            )
        )
        session.add(profile)
        await session.commit()
    await login(client, user.username, "correct horse battery staple")
    request_id = await _request(client)

    responses = [
        await client.post(
            f"/api/requests/{request_id}/grab",
            json={"release_id": str(release.id)},
            headers=csrf_headers(client),
        )
        for release in (blocked, library, filtered)
    ]

    assert [response.json()["code"] for response in responses] == [
        "RELEASE_BLOCKED",
        "RELEASE_IN_LIBRARY",
        "RELEASE_FILTERED",
    ]


async def test_grab_without_a_healthy_matching_client_is_refused(app, client) -> None:
    user = await create_user(app)
    async with AsyncSession(app.state.engine, expire_on_commit=False) as session:
        release = await _release(session)
        await session.commit()
    await login(client, user.username, "correct horse battery staple")
    request_id = await _request(client)

    response = await client.post(
        f"/api/requests/{request_id}/grab",
        json={"release_id": str(release.id)},
        headers=csrf_headers(client),
    )

    assert response.status_code == 409
    assert response.json()["code"] == "DOWNLOAD_CLIENT_UNAVAILABLE"
