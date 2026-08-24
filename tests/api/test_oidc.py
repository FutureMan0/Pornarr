"""OIDC provider administration without a real identity provider."""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import cast
from uuid import UUID

import httpcore
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from pornarr_api.auth import hash_password
from pornarr_api.oidc import GuardedNetworkBackend, OidcDiscoveryError, discover, normalise_issuer
from pornarr_db.models.oidc import OidcProvider
from pornarr_db.models.user import User, UserRole
from pornarr_db.types import set_cipher
from pornarr_shared.crypto import CredentialCipher
from tests.api.test_app import SECRET
from tests.api.test_auth import csrf_headers, login

pytest_plugins = ("tests.api.test_auth",)


class RecordingNetworkBackend(httpcore.AsyncNetworkBackend):
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.stream = cast(httpcore.AsyncNetworkStream, object())

    async def connect_tcp(self, host: str, *_: object, **__: object) -> httpcore.AsyncNetworkStream:
        self.calls.append(host)
        return self.stream

    async def connect_unix_socket(self, *_: object, **__: object) -> httpcore.AsyncNetworkStream:
        raise AssertionError("OIDC discovery must not use a Unix socket")

    async def sleep(self, seconds: float) -> None:
        _ = seconds
        raise AssertionError("OIDC discovery does not retry connections")


@pytest.fixture(autouse=True)
def _cipher() -> Iterator[None]:
    set_cipher(CredentialCipher(SECRET))
    yield
    set_cipher(None)


async def create_admin(application) -> User:
    async with AsyncSession(application.state.engine, expire_on_commit=False) as session:
        admin = User(
            username="admin",
            password_hash=hash_password("correct horse battery staple"),
            role=UserRole.ADMIN,
        )
        session.add(admin)
        await session.commit()
    return admin


async def test_admin_can_create_list_and_delete_a_provider(app, client) -> None:
    admin = await create_admin(app)
    await login(client, admin.username, "correct horse battery staple")
    payload = {
        "name": "example",
        "issuer": "https://issuer.example/",
        "client_id": "client-id",
        "client_secret": "secret-value",
        "username_claim": "email",
        "role_claim": "roles",
        "role_mapping": {"administrators": "admin"},
        "default_role": "user",
        "required_claim": "tenant",
        "required_claim_value": "trusted",
    }

    created = await client.post("/api/admin/oidc", json=payload, headers=csrf_headers(client))

    assert created.status_code == 201
    assert created.json()["issuer"] == "https://issuer.example"
    assert created.json()["role_mapping"] == {"administrators": "admin"}
    assert created.json()["required_claim"] == "tenant"
    assert created.json()["required_claim_value"] == "trusted"
    assert "client_secret" not in created.text
    provider_id = created.json()["id"]
    assert (await client.get("/api/admin/oidc")).json()[0]["id"] == provider_id

    async with AsyncSession(app.state.engine) as session:
        provider = await session.get(OidcProvider, UUID(provider_id))
        assert provider is not None
        assert provider.client_secret == "secret-value"
        raw = await session.scalar(text("select client_secret from oidc_providers"))
        assert raw != "secret-value"

    assert (
        await client.delete(f"/api/admin/oidc/{provider_id}", headers=csrf_headers(client))
    ).status_code == 204


async def test_regular_users_cannot_manage_providers(app, client) -> None:
    async with AsyncSession(app.state.engine, expire_on_commit=False) as session:
        user = User(username="user", password_hash=hash_password("correct horse battery staple"))
        session.add(user)
        await session.commit()
    await login(client, user.username, "correct horse battery staple")

    response = await client.get("/api/admin/oidc")

    assert response.status_code == 403


@pytest.mark.parametrize("issuer", ["http://issuer.example", "https://issuer.example?next=bad"])
async def test_admin_cannot_store_an_unsafe_issuer(app, client, issuer: str) -> None:
    admin = await create_admin(app)
    await login(client, admin.username, "correct horse battery staple")

    response = await client.post(
        "/api/admin/oidc",
        json={
            "name": "unsafe",
            "issuer": issuer,
            "client_id": "client-id",
            "client_secret": "secret-value",
        },
        headers=csrf_headers(client),
    )

    assert response.status_code == 422


async def test_connection_test_caches_the_discovery_document(app, client, monkeypatch) -> None:
    admin = await create_admin(app)
    await login(client, admin.username, "correct horse battery staple")
    created = await client.post(
        "/api/admin/oidc",
        json={
            "name": "example",
            "issuer": "https://issuer.example",
            "client_id": "client-id",
            "client_secret": "secret-value",
        },
        headers=csrf_headers(client),
    )

    async def fake_discover(_: str, **__: object) -> dict[str, str]:
        return {
            "issuer": "https://issuer.example",
            "authorization_endpoint": "https://issuer.example/authorize",
            "token_endpoint": "https://issuer.example/token",
            "jwks_uri": "https://issuer.example/jwks",
        }

    monkeypatch.setattr("pornarr_api.routers.admin_oidc.discover", fake_discover)
    tested = await client.post(
        f"/api/admin/oidc/{created.json()['id']}/test", headers=csrf_headers(client)
    )

    assert tested.status_code == 200
    assert tested.json()["discovery_fetched_at"] is not None
    async with AsyncSession(app.state.engine) as session:
        provider = await session.get(OidcProvider, UUID(created.json()["id"]))
        assert provider is not None
        assert provider.discovery_document is not None
        assert provider.discovery_document["jwks_uri"] == "https://issuer.example/jwks"


async def test_discovery_validates_the_document(monkeypatch) -> None:
    document = {
        "issuer": "https://issuer.example",
        "authorization_endpoint": "https://issuer.example/authorize",
        "token_endpoint": "https://issuer.example/token",
        "jwks_uri": "https://issuer.example/jwks",
    }

    class Pool:
        async def __aenter__(self) -> Pool:
            return self

        async def __aexit__(self, *_: object) -> None:
            return None

        async def request(self, _: str, url: str, **__: object) -> object:
            assert url == "https://issuer.example/.well-known/openid-configuration"
            return type("Response", (), {"status": 200, "content": json.dumps(document).encode()})()

    monkeypatch.setattr("pornarr_api.oidc.httpcore.AsyncConnectionPool", lambda **_: Pool())

    assert await discover("https://issuer.example/") == document


async def test_discovery_rejects_an_invalid_document(monkeypatch) -> None:
    class Pool:
        async def __aenter__(self) -> Pool:
            return self

        async def __aexit__(self, *_: object) -> None:
            return None

        async def request(self, *_: object, **__: object) -> object:
            return type(
                "Response", (), {"status": 200, "content": b'{"issuer": "https://wrong.example"}'}
            )()

    monkeypatch.setattr("pornarr_api.oidc.httpcore.AsyncConnectionPool", lambda **_: Pool())

    with pytest.raises(OidcDiscoveryError):
        await discover("https://issuer.example")


@pytest.mark.parametrize(
    "issuer",
    [
        "http://issuer.example",
        "https://user:password@issuer.example",
        "https://issuer.example?",
        "https://issuer.example?next=https://other.example",
        "https://issuer.example#",
        "https://issuer.example#fragment",
    ],
)
def test_discovery_rejects_unsafe_issuer_urls(issuer: str) -> None:
    with pytest.raises(OidcDiscoveryError):
        normalise_issuer(issuer)


async def test_discovery_network_backend_blocks_private_ip_literals() -> None:
    delegate = RecordingNetworkBackend()

    backend = GuardedNetworkBackend(
        allow_private_issuers=False,
        resolver=lambda host, port: (host,),
        backend=delegate,
    )

    with pytest.raises(httpcore.ConnectError):
        await backend.connect_tcp("127.0.0.1", 443)

    assert delegate.calls == []


async def test_discovery_network_backend_connects_to_a_validated_public_dns_result() -> None:
    delegate = RecordingNetworkBackend()

    backend = GuardedNetworkBackend(
        allow_private_issuers=False,
        resolver=lambda _host, _port: ("127.0.0.1", "93.184.216.34"),
        backend=delegate,
    )

    assert await backend.connect_tcp("issuer.example", 443) is delegate.stream
    assert delegate.calls == ["93.184.216.34"]


async def test_discovery_network_backend_allows_private_ips_only_when_opted_in() -> None:
    delegate = RecordingNetworkBackend()

    backend = GuardedNetworkBackend(
        allow_private_issuers=True,
        resolver=lambda _host, _port: ("127.0.0.1",),
        backend=delegate,
    )

    await backend.connect_tcp("issuer.example", 443)

    assert delegate.calls == ["127.0.0.1"]


async def test_discovery_rejects_redirects_without_following_them(monkeypatch) -> None:
    requests: list[str] = []

    class Pool:
        async def __aenter__(self) -> Pool:
            return self

        async def __aexit__(self, *_: object) -> None:
            return None

        async def request(self, _: str, url: str, **__: object) -> object:
            requests.append(url)
            return type("Response", (), {"status": 302, "content": b""})()

    monkeypatch.setattr("pornarr_api.oidc.httpcore.AsyncConnectionPool", lambda **_: Pool())

    with pytest.raises(OidcDiscoveryError):
        await discover("https://issuer.example")

    assert requests == ["https://issuer.example/.well-known/openid-configuration"]


async def test_missing_provider_returns_not_found(app, client) -> None:
    admin = await create_admin(app)
    await login(client, admin.username, "correct horse battery staple")

    response = await client.delete(
        "/api/admin/oidc/00000000-0000-0000-0000-000000000000", headers=csrf_headers(client)
    )

    assert response.status_code == 404
