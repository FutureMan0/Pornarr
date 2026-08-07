"""OIDC provider administration without a real identity provider."""

from __future__ import annotations

from collections.abc import Iterator
from uuid import UUID

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from pornarr_api.auth import hash_password
from pornarr_api.oidc import OidcDiscoveryError, discover
from pornarr_db.models.oidc import OidcProvider
from pornarr_db.models.user import User, UserRole
from pornarr_db.types import set_cipher
from pornarr_shared.crypto import CredentialCipher
from tests.api.test_app import SECRET
from tests.api.test_auth import csrf_headers, login

pytest_plugins = ("tests.api.test_auth",)


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
    }

    created = await client.post("/api/admin/oidc", json=payload, headers=csrf_headers(client))

    assert created.status_code == 201
    assert created.json()["issuer"] == "https://issuer.example"
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

    async def fake_discover(_: str) -> dict[str, str]:
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

    class Response:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, str]:
            return document

    class Client:
        async def __aenter__(self) -> Client:
            return self

        async def __aexit__(self, *_: object) -> None:
            return None

        async def get(self, url: str) -> Response:
            assert url == "https://issuer.example/.well-known/openid-configuration"
            return Response()

    monkeypatch.setattr("pornarr_api.oidc.httpx.AsyncClient", lambda **_: Client())

    assert await discover("https://issuer.example/") == document


async def test_discovery_rejects_an_invalid_document(monkeypatch) -> None:
    class Response:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, str]:
            return {"issuer": "https://wrong.example"}

    class Client:
        async def __aenter__(self) -> Client:
            return self

        async def __aexit__(self, *_: object) -> None:
            return None

        async def get(self, _: str) -> Response:
            return Response()

    monkeypatch.setattr("pornarr_api.oidc.httpx.AsyncClient", lambda **_: Client())

    with pytest.raises(OidcDiscoveryError):
        await discover("https://issuer.example")


async def test_missing_provider_returns_not_found(app, client) -> None:
    admin = await create_admin(app)
    await login(client, admin.username, "correct horse battery staple")

    response = await client.delete(
        "/api/admin/oidc/00000000-0000-0000-0000-000000000000", headers=csrf_headers(client)
    )

    assert response.status_code == 404
