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
