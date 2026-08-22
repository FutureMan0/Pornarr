"""Administrator download-client configuration and routing."""

from __future__ import annotations

from collections.abc import Iterator
from uuid import UUID

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from pornarr_db.download_clients import NoHealthyDownloadClientError, route_download_client
from pornarr_db.models.download_client import DownloadClient
from pornarr_db.models.user import UserRole
from pornarr_db.types import set_cipher
from pornarr_integrations.downloaders import DownloadClientAdapter
from pornarr_shared.crypto import CredentialCipher
from tests.api.test_app import SECRET
from tests.api.test_auth import create_user, csrf_headers, login

pytest_plugins = ("tests.api.test_auth",)


@pytest.fixture(autouse=True)
def _cipher() -> Iterator[None]:
    set_cipher(CredentialCipher(SECRET))
    yield
    set_cipher(None)


class WorkingAdapter:
    async def test_connection(
        self, *, host: str, port: int, url_base: str, credentials: str
    ) -> None:
        assert (host, port, url_base, credentials) == ("client.example", 8080, "/api", "secret")


class FailingAdapter:
    async def test_connection(
        self, *, host: str, port: int, url_base: str, credentials: str
    ) -> None:
        raise ConnectionError(f"{host}:{port} rejected {credentials}")


async def test_admin_can_configure_test_and_route_a_download_client(app, client) -> None:
    admin = await create_user(app, username="admin", role=UserRole.ADMIN)
    app.state.download_client_adapters = {"qbittorrent": WorkingAdapter()}
    await login(client, admin.username, "correct horse battery staple")
    payload = {
        "name": "torrent",
        "protocol": "torrent",
        "implementation": "qbittorrent",
        "host": "client.example",
        "port": 8080,
        "url_base": "/api",
        "credentials": "secret",
        "category": "pornarr",
        "priority": 1,
        "remove_completed": False,
    }

    created = await client.post(
        "/api/admin/download-clients", json=payload, headers=csrf_headers(client)
    )

    assert created.status_code == 201
    assert "credentials" not in created.text
    client_id = UUID(created.json()["id"])
    async with AsyncSession(app.state.engine) as session:
        stored = await session.get(DownloadClient, client_id)
        assert stored is not None and stored.credentials == "secret"
        assert await session.scalar(text("select credentials from download_clients")) != "secret"

    tested = await client.post(
        f"/api/admin/download-clients/{client_id}/test", headers=csrf_headers(client)
    )

    assert tested.status_code == 200
    assert tested.json()["health"] == "healthy"
    async with AsyncSession(app.state.engine) as session:
        routed = await route_download_client(session, "torrent")
        assert routed.id == client_id
        with pytest.raises(NoHealthyDownloadClientError):
            await route_download_client(session, "usenet")


async def test_a_failed_connection_test_is_remembered_on_the_client_row(app, client) -> None:
    """The route re-raises after a failed test, and `database_session` rolls
    back on any exception it sees - so the diagnosis has to be committed before
    that raise or the operator's failed test leaves no trace on the row.
    """
    admin = await create_user(app, username="admin", role=UserRole.ADMIN)
    adapters: dict[str, DownloadClientAdapter] = {"qbittorrent": FailingAdapter()}
    app.state.download_client_adapters = adapters
    await login(client, admin.username, "correct horse battery staple")
    payload = {
        "name": "torrent",
        "protocol": "torrent",
        "implementation": "qbittorrent",
        "host": "client.example",
        "port": 8080,
        "url_base": "/api",
        "credentials": "secret",
        "category": "pornarr",
        "priority": 1,
        "remove_completed": False,
    }
    created = await client.post(
        "/api/admin/download-clients", json=payload, headers=csrf_headers(client)
    )
    client_id = created.json()["id"]

    failed = await client.post(
        f"/api/admin/download-clients/{client_id}/test", headers=csrf_headers(client)
    )

    assert failed.status_code == 422
    assert failed.json()["code"] == "DOWNLOAD_CLIENT_CONNECTION_FAILED"
    assert "secret" not in failed.text
    stored = (await client.get("/api/admin/download-clients")).json()[0]
    assert stored["health"] == "unhealthy"
    assert stored["last_error"] is not None
    assert "secret" not in stored["last_error"]

    adapters["qbittorrent"] = WorkingAdapter()
    healed = await client.post(
        f"/api/admin/download-clients/{client_id}/test", headers=csrf_headers(client)
    )

    assert healed.status_code == 200
    assert healed.json()["health"] == "healthy"
    assert healed.json()["last_error"] is None


def _client_row(name: str, **overrides: object) -> DownloadClient:
    """One configured instance, with everything routing looks at spelled out."""
    fields: dict[str, object] = {
        "name": name,
        "protocol": "torrent",
        "implementation": "qbittorrent",
        "host": f"{name}.example",
        "port": 8080,
        "url_base": "",
        "credentials": "secret",
        "category": "pornarr",
        "priority": 0,
        "enabled": True,
        "health": "healthy",
    }
    fields.update(overrides)
    return DownloadClient(**fields)  # type: ignore[arg-type]


async def test_a_grab_is_routed_by_protocol_then_priority_then_health(app) -> None:
    """ADR 0003: multiple instances per protocol, chosen in that documented order.

    Every leg is a separate row rather than a separate assertion on one row, so
    a routing rule that quietly stopped consulting one of the three fields
    changes which name comes back.
    """
    async with AsyncSession(app.state.engine) as session:
        rows = [
            _client_row("usenet-first", protocol="usenet", implementation="sabnzbd", priority=-10),
            _client_row("torrent-disabled", priority=-5, enabled=False),
            _client_row("torrent-unhealthy", priority=-4, health="unhealthy"),
            _client_row("torrent-untested", priority=-3, health="unknown"),
            _client_row("torrent-preferred", priority=1),
            _client_row("torrent-fallback", priority=2),
            _client_row("torrent-alphabetically-first", priority=1),
        ]
        session.add_all(rows)
        await session.flush()

        # Protocol first: the usenet row has the best priority of all and is
        # never considered for a torrent.
        chosen = await route_download_client(session, "torrent")
        # Then priority, with the name as the tie-break, so routing is
        # deterministic when two instances are configured equally.
        assert chosen.name == "torrent-alphabetically-first"
        assert (await route_download_client(session, "usenet")).name == "usenet-first"

        # Then health: with the winner gone, disabled, unhealthy and untested
        # instances are all skipped even though every one of them has a better
        # priority than what actually gets picked.
        await session.delete(chosen)
        await session.flush()
        assert (await route_download_client(session, "torrent")).name == "torrent-preferred"

        for name in ("torrent-preferred", "torrent-fallback"):
            row = next(item for item in rows if item.name == name)
            await session.delete(row)
        await session.flush()
        with pytest.raises(NoHealthyDownloadClientError) as refusal:
            await route_download_client(session, "torrent")
        assert refusal.value.code == "DOWNLOAD_CLIENT_UNAVAILABLE"
        assert refusal.value.status == 409


async def test_two_instances_of_one_protocol_coexist_and_are_both_addressable(app, client) -> None:
    """ADR 0003 L9: "Multiple instances per protocol are supported"."""
    admin = await create_user(app, username="two-instances-admin", role=UserRole.ADMIN)
    app.state.download_client_adapters = {"qbittorrent": WorkingAdapter()}
    await login(client, admin.username, "correct horse battery staple")
    base = {
        "protocol": "torrent",
        "implementation": "qbittorrent",
        "host": "client.example",
        "port": 8080,
        "url_base": "/api",
        "credentials": "secret",
        "category": "pornarr",
        "remove_completed": False,
    }

    created = [
        await client.post(
            "/api/admin/download-clients",
            json={**base, "name": name, "priority": priority},
            headers=csrf_headers(client),
        )
        for name, priority in (("seedbox", 1), ("desktop", 2))
    ]

    assert [response.status_code for response in created] == [201, 201]
    for response in created:
        tested = await client.post(
            f"/api/admin/download-clients/{response.json()['id']}/test",
            headers=csrf_headers(client),
        )
        assert tested.status_code == 200
    listed = (await client.get("/api/admin/download-clients")).json()
    torrent_rows = [row for row in listed if row["protocol"] == "torrent"]
    assert {row["name"] for row in torrent_rows} >= {"seedbox", "desktop"}
    async with AsyncSession(app.state.engine) as session:
        assert (await route_download_client(session, "torrent")).name == "seedbox"


async def test_editing_a_client_without_resending_its_secret_keeps_the_stored_one(
    app, client
) -> None:
    """The convention the indexers route already follows.

    A client's credential is write-only: the response carries everything about
    it except its secret, so a screen editing a client has nothing to put back
    in the box. `IndexerUpdate.api_key` is `None`-able for exactly that reason
    and means "keep the stored key". `DownloadClientUpdate` required the
    credential, so an edit form that left the box empty wrote an empty string -
    and the client stopped answering. Re-pointing a client to a new port meant
    re-typing its password from memory.
    """

    admin = await create_user(app, username="admin", role=UserRole.ADMIN)
    await login(client, admin.username, "correct horse battery staple")
    payload = {
        "name": "torrent",
        "protocol": "torrent",
        "implementation": "qbittorrent",
        "host": "client.example",
        "port": 8080,
        "url_base": "",
        "credentials": "the stored one",
        "category": None,
        "priority": 0,
        "remove_completed": False,
    }
    created = await client.post(
        "/api/admin/download-clients", json=payload, headers=csrf_headers(client)
    )
    assert created.status_code == 201
    client_id = UUID(created.json()["id"])

    moved = await client.put(
        f"/api/admin/download-clients/{client_id}",
        json={**payload, "port": 9090, "credentials": None},
        headers=csrf_headers(client),
    )

    assert moved.status_code == 200
    assert moved.json()["port"] == 9090
    async with AsyncSession(app.state.engine) as session:
        stored = await session.get(DownloadClient, client_id)
        assert stored is not None
        assert stored.credentials == "the stored one"

    # And a credential that *is* sent replaces the stored one.
    replaced = await client.put(
        f"/api/admin/download-clients/{client_id}",
        json={**payload, "credentials": "a new one"},
        headers=csrf_headers(client),
    )

    assert replaced.status_code == 200
    async with AsyncSession(app.state.engine) as session:
        stored = await session.get(DownloadClient, client_id)
        assert stored is not None
        assert stored.credentials == "a new one"
