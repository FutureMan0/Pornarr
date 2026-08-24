"""Per-user API keys are one-time secrets with immediate revocation."""

from __future__ import annotations

from httpx import ASGITransport, AsyncClient

from pornarr_db.models.user import UserRole
from tests.api.test_auth import create_user, csrf_headers, login

pytest_plugins = ("tests.api.test_auth",)


async def test_api_key_is_shown_once_authenticates_and_can_be_revoked(app, client) -> None:
    user = await create_user(app)
    await login(client, user.username, "correct horse battery staple")

    created = await client.post(
        "/api/account/api-keys", json={"label": "script"}, headers=csrf_headers(client)
    )

    assert created.status_code == 201
    plaintext = created.json()["key"]
    assert plaintext.startswith("pnr_")
    assert "key" not in (await client.get("/api/account/api-keys")).text

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as key_client:
        assert (
            await key_client.get("/api/auth/me", headers={"X-Api-Key": plaintext})
        ).status_code == 401
        assert (
            await key_client.get(
                "/api/playback/continue-watching", headers={"X-Api-Key": plaintext}
            )
        ).status_code == 200
    assert (
        await client.delete(
            f"/api/account/api-keys/{created.json()['id']}", headers=csrf_headers(client)
        )
    ).status_code == 204
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as key_client:
        assert (
            await key_client.get(
                "/api/playback/continue-watching", headers={"X-Api-Key": plaintext}
            )
        ).status_code == 401


async def test_api_key_cannot_change_authentication_settings(app, client) -> None:
    admin = await create_user(app, role=UserRole.ADMIN)
    await login(client, admin.username, "correct horse battery staple")
    created = await client.post(
        "/api/account/api-keys", json={"label": "script"}, headers=csrf_headers(client)
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as key_client:
        response = await key_client.post(
            "/api/admin/oidc",
            json={
                "name": "example",
                "issuer": "https://issuer.example",
                "client_id": "client-id",
                "client_secret": "secret-value",
            },
            headers={"X-Api-Key": created.json()["key"]},
        )

    assert response.status_code == 403
