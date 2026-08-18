"""Configuring the providers the import cascade asks."""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import create_async_engine

from pornarr_api.main import create_app
from pornarr_db.base import Base
from pornarr_db.models.user import UserRole
from tests.api.test_app import build_settings
from tests.api.test_auth import MemoryRedis, create_user, csrf_headers, login


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


async def test_a_provider_key_is_stored_once_per_implementation_and_never_returned(
    app, client: AsyncClient
) -> None:
    admin = await create_user(app, username="admin", role=UserRole.ADMIN)
    await login(client, admin.username, "correct horse battery staple")

    created = await client.post(
        "/api/admin/metadata-providers",
        json={"implementation": "stashdb", "api_key": "first-key"},
        headers=csrf_headers(client),
    )

    assert created.status_code == 201
    assert "api_key" not in created.json()
    assert created.json()["implementation"] == "stashdb"

    # Configuring it again replaces the key rather than asking StashDB twice.
    replaced = await client.post(
        "/api/admin/metadata-providers",
        json={"implementation": "stashdb", "api_key": "second-key", "priority": 5},
        headers=csrf_headers(client),
    )
    assert replaced.status_code == 201
    assert replaced.json()["id"] == created.json()["id"]

    listed = await client.get("/api/admin/metadata-providers")
    assert [provider["implementation"] for provider in listed.json()] == ["stashdb"]
    assert listed.json()[0]["priority"] == 5

    removed = await client.delete(
        f"/api/admin/metadata-providers/{created.json()['id']}", headers=csrf_headers(client)
    )
    assert removed.status_code == 204
    assert (await client.get("/api/admin/metadata-providers")).json() == []


async def test_an_unknown_provider_is_refused(app, client: AsyncClient) -> None:
    admin = await create_user(app, username="admin", role=UserRole.ADMIN)
    await login(client, admin.username, "correct horse battery staple")

    response = await client.post(
        "/api/admin/metadata-providers",
        json={"implementation": "not-a-provider", "api_key": "key"},
        headers=csrf_headers(client),
    )

    assert response.status_code == 422


async def test_only_an_administrator_can_read_the_provider_list(app, client: AsyncClient) -> None:
    user = await create_user(app)
    await login(client, user.username, "correct horse battery staple")

    assert (await client.get("/api/admin/metadata-providers")).status_code == 403
