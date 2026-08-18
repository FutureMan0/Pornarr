"""Federation without a second server.

A peer is stood in for by a transport that answers the calls this instance
makes, which is enough to pin down the two things that matter: that the key
issued by the other instance never comes back out, and that the proxy fetches
what is on the allowlist and nothing else.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from pornarr_db.models.peer import Peer
from pornarr_db.models.user import UserRole
from pornarr_db.types import set_cipher
from pornarr_shared.crypto import CredentialCipher
from tests.api.test_app import SECRET
from tests.api.test_auth import create_user, csrf_headers, login

pytest_plugins = ["tests.api.test_auth"]

PEER_KEY = "pnr_remote-instance-key"
MEDIA_ID = "2a1c6a3c-5f52-4f0e-9d2f-7f8a6f3d1b21"
POSTER = f"media/{MEDIA_ID}/poster"


@pytest.fixture(autouse=True)
def _cipher() -> Iterator[None]:
    set_cipher(CredentialCipher(SECRET))
    yield
    set_cipher(None)


def install_peer(app: FastAPI, handler) -> list[httpx.Request]:
    """Answer this instance's outgoing peer calls, and record them."""

    seen: list[httpx.Request] = []

    def record(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    app.state.peer_transport = httpx.MockTransport(record)
    return seen


def streamed(status: int, body: bytes, headers: dict[str, str]) -> httpx.Response:
    """A response whose body is still on the wire, as a real peer's would be."""

    async def chunks() -> AsyncIterator[bytes]:
        yield body

    return httpx.Response(status, content=chunks(), headers=headers)


def library_page(total: int = 3) -> dict[str, object]:
    return {"items": [], "total": total, "next_offset": None}


async def register(client: AsyncClient, **overrides: object) -> dict[str, object]:
    payload = {
        "name": "friend",
        "base_url": "https://friend.example/api/",
        "api_key": PEER_KEY,
        "enabled": True,
    } | overrides
    response = await client.post("/api/admin/peers", json=payload, headers=csrf_headers(client))
    assert response.status_code == 201, response.text
    return response.json()


async def admin_client(app: FastAPI, client: AsyncClient) -> None:
    admin = await create_user(app, username="admin", role=UserRole.ADMIN)
    await login(client, admin.username, "correct horse battery staple")


async def test_a_peer_is_stored_encrypted_and_its_key_never_comes_back(
    app: FastAPI, client: AsyncClient
) -> None:
    await admin_client(app, client)

    created = await register(client)

    assert PEER_KEY not in created.__str__()
    assert created["health"] == "unknown"
    assert created["media_count"] is None
    # The trailing slash is dropped once, here, so every later URL is built the
    # same way instead of each call site guessing.
    assert created["base_url"] == "https://friend.example/api"
    listed = await client.get("/api/admin/peers")
    assert listed.status_code == 200
    assert "api_key" not in listed.text
    assert PEER_KEY not in listed.text
    async with AsyncSession(app.state.engine) as session:
        stored = await session.get(Peer, UUID(str(created["id"])))
        assert stored is not None
        assert stored.api_key == PEER_KEY
    removed = await client.delete(f"/api/admin/peers/{created['id']}", headers=csrf_headers(client))
    assert removed.status_code == 204
    assert (await client.get("/api/admin/peers")).json() == []


async def test_only_an_administrator_configures_peers(app: FastAPI, client: AsyncClient) -> None:
    user = await create_user(app)
    await login(client, user.username, "correct horse battery staple")

    assert (await client.get("/api/admin/peers")).status_code == 403
    created = await client.post(
        "/api/admin/peers",
        json={"name": "friend", "base_url": "https://friend.example/api", "api_key": PEER_KEY},
        headers=csrf_headers(client),
    )
    assert created.status_code == 403


async def test_a_peer_that_is_not_a_url_is_refused(app: FastAPI, client: AsyncClient) -> None:
    """`file:///etc/passwd` is a base URL too, if nobody checks."""

    await admin_client(app, client)

    created = await client.post(
        "/api/admin/peers",
        json={"name": "friend", "base_url": "file:///etc/passwd", "api_key": PEER_KEY},
        headers=csrf_headers(client),
    )

    assert created.status_code == 422


async def test_testing_a_peer_records_what_it_answered(app: FastAPI, client: AsyncClient) -> None:
    await admin_client(app, client)
    peer = await register(client)
    calls = install_peer(app, lambda _: httpx.Response(200, json=library_page(total=7)))

    tested = await client.post(f"/api/admin/peers/{peer['id']}/test", headers=csrf_headers(client))

    assert tested.status_code == 200
    assert tested.json()["health"] == "healthy"
    assert tested.json()["health_reason"] is None
    assert tested.json()["media_count"] == 7
    assert tested.json()["last_tested_at"] is not None
    assert str(calls[0].url) == "https://friend.example/api/library?limit=1"
    assert calls[0].headers["X-Api-Key"] == PEER_KEY


@pytest.mark.parametrize(
    ("answer", "reason"),
    [
        (httpx.Response(401), "unauthorized"),
        (httpx.Response(500), "invalid_response"),
        (httpx.Response(200, text="<html>login</html>"), "invalid_response"),
    ],
)
async def test_a_peer_that_says_no_is_recorded_as_a_short_reason(
    app: FastAPI, client: AsyncClient, answer: httpx.Response, reason: str
) -> None:
    """A reason, not an exception text: the text carries the URL the key went to."""

    await admin_client(app, client)
    peer = await register(client)
    install_peer(app, lambda _: answer)

    tested = await client.post(f"/api/admin/peers/{peer['id']}/test", headers=csrf_headers(client))

    assert tested.json()["health"] == "unhealthy"
    assert tested.json()["health_reason"] == reason
    assert PEER_KEY not in tested.text


async def test_a_peer_that_never_answers_does_not_hang_the_screen(
    app: FastAPI, client: AsyncClient
) -> None:
    await admin_client(app, client)
    peer = await register(client)

    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("timed out", request=request)

    install_peer(app, refuse)

    tested = await client.post(f"/api/admin/peers/{peer['id']}/test", headers=csrf_headers(client))

    assert tested.json()["health"] == "unhealthy"
    assert tested.json()["health_reason"] == "timed_out"


async def test_the_proxy_fetches_a_poster_with_the_stored_key(
    app: FastAPI, client: AsyncClient
) -> None:
    await admin_client(app, client)
    peer = await register(client)
    calls = install_peer(
        app,
        lambda _: streamed(
            200,
            b"\xff\xd8jpeg",
            {
                "Content-Type": "image/jpeg",
                "Cache-Control": "private, max-age=3600",
                # A peer has no business setting anything in this browser.
                "Set-Cookie": "peer_session=abc; Path=/",
            },
        ),
    )

    fetched = await client.get(f"/api/peers/{peer['id']}/proxy/{POSTER}")

    assert fetched.status_code == 200
    assert fetched.content == b"\xff\xd8jpeg"
    assert fetched.headers["content-type"] == "image/jpeg"
    assert fetched.headers["cache-control"] == "private, max-age=3600"
    assert "set-cookie" not in fetched.headers
    assert calls[0].headers["X-Api-Key"] == PEER_KEY
    assert "cookie" not in {name.lower() for name in calls[0].headers}


async def test_the_proxy_carries_a_range_through_untouched(
    app: FastAPI, client: AsyncClient
) -> None:
    """Seeking a remote film is a range request or it is nothing."""

    await admin_client(app, client)
    peer = await register(client)
    calls = install_peer(
        app,
        lambda _: streamed(
            206,
            b"bytes",
            {"Content-Range": "bytes 10-14/999", "Accept-Ranges": "bytes"},
        ),
    )
    path = "media/2a1c6a3c-5f52-4f0e-9d2f-7f8a6f3d1b21/stream"

    fetched = await client.get(
        f"/api/peers/{peer['id']}/proxy/{path}", headers={"Range": "bytes=10-14"}
    )

    assert fetched.status_code == 206
    assert fetched.headers["content-range"] == "bytes 10-14/999"
    assert calls[0].headers["Range"] == "bytes=10-14"


async def test_a_remote_title_can_be_opened_through_the_proxy(app, client) -> None:
    """A card in a merged library has to lead somewhere, and its id is the peer's."""

    await admin_client(app, client)
    peer = await register(client)
    calls = install_peer(
        app,
        lambda _: streamed(
            200,
            b'{"title": "Remote"}',
            {"Content-Type": "application/json"},
        ),
    )

    response = await client.get(f"/api/peers/{peer['id']}/proxy/media/{MEDIA_ID}")

    assert response.status_code == 200
    assert response.json()["title"] == "Remote"
    assert [call.url.path for call in calls] == [f"/api/media/{MEDIA_ID}"]


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "admin/settings"),
        ("GET", "../admin/peers"),
        ("GET", "library/facets"),
        ("POST", "library"),
        ("DELETE", "media/2a1c6a3c-5f52-4f0e-9d2f-7f8a6f3d1b21/poster"),
        ("POST", "ratings/2a1c6a3c-5f52-4f0e-9d2f-7f8a6f3d1b21"),
        ("GET", "transcode/sessions/2a1c6a3c-5f52-4f0e-9d2f-7f8a6f3d1b21/hls/../../secret"),
    ],
)
async def test_the_proxy_is_not_an_open_relay(
    app: FastAPI, client: AsyncClient, method: str, path: str
) -> None:
    await admin_client(app, client)
    peer = await register(client)
    calls = install_peer(app, lambda _: httpx.Response(200, json={}))

    blocked = await client.request(
        method, f"/api/peers/{peer['id']}/proxy/{path}", headers=csrf_headers(client)
    )

    assert blocked.status_code == 404
    assert calls == []


async def test_a_disabled_peer_is_not_fetched_from(app: FastAPI, client: AsyncClient) -> None:
    await admin_client(app, client)
    peer = await register(client, enabled=False)
    calls = install_peer(app, lambda _: httpx.Response(200, content=b"poster"))

    blocked = await client.get(f"/api/peers/{peer['id']}/proxy/{POSTER}")

    assert blocked.status_code == 404
    assert calls == []


async def test_an_unknown_peer_is_not_fetched_from(app: FastAPI, client: AsyncClient) -> None:
    await admin_client(app, client)

    blocked = await client.get(f"/api/peers/{uuid4()}/proxy/{POSTER}")

    assert blocked.status_code == 404


async def test_the_proxy_requires_a_session(app: FastAPI, client: AsyncClient) -> None:
    """Otherwise the household's tunnel is an anonymous mirror of a friend's server."""

    admin = await create_user(app, username="admin", role=UserRole.ADMIN)
    await login(client, admin.username, "correct horse battery staple")
    peer = await register(client)
    client.cookies.clear()

    blocked = await client.get(f"/api/peers/{peer['id']}/proxy/{POSTER}")

    assert blocked.status_code == 401


async def test_a_peer_that_rejects_this_instance_is_reported_as_unavailable(
    app: FastAPI, client: AsyncClient
) -> None:
    """Passing 401 through would send the reader to their own login screen."""

    await admin_client(app, client)
    peer = await register(client)
    install_peer(app, lambda _: httpx.Response(403))

    fetched = await client.get(f"/api/peers/{peer['id']}/proxy/{POSTER}")

    assert fetched.status_code == 502
    assert fetched.json()["code"] == "PEER_UNAVAILABLE"


async def test_a_peer_that_is_down_is_reported_as_unavailable(
    app: FastAPI, client: AsyncClient
) -> None:
    await admin_client(app, client)
    peer = await register(client)

    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route to host", request=request)

    install_peer(app, refuse)

    fetched = await client.get(f"/api/peers/{peer['id']}/proxy/{POSTER}")

    assert fetched.status_code == 502
    assert fetched.json()["code"] == "PEER_UNAVAILABLE"
    assert "no route to host" not in fetched.text
