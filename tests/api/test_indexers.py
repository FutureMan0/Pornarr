"""Administrator indexer configuration without a real indexer server."""

from __future__ import annotations

from collections.abc import Iterator
from uuid import UUID

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from pornarr_db.models.indexer import Indexer
from pornarr_db.models.user import UserRole
from pornarr_db.types import set_cipher
from pornarr_integrations.health import CircuitBreaker
from pornarr_integrations.indexers import IndexerCategory
from pornarr_shared.crypto import CredentialCipher
from tests.api.test_app import SECRET
from tests.api.test_auth import create_user, csrf_headers, login

pytest_plugins = ["tests.api.test_auth"]


@pytest.fixture(autouse=True)
def _cipher() -> Iterator[None]:
    set_cipher(CredentialCipher(SECRET))
    yield
    set_cipher(None)


class WorkingAdapter:
    async def test_connection(self, *, base_url: str, api_key: str) -> list[IndexerCategory]:
        assert base_url == "https://indexer.example"
        assert api_key == "secret-value"
        return [IndexerCategory("5000", "TV")]


class FailingAdapter:
    async def test_connection(self, *, base_url: str, api_key: str) -> list[IndexerCategory]:
        raise ConnectionError(f"{base_url} rejected {api_key}")


async def test_admin_can_configure_test_and_remove_an_indexer(app, client) -> None:
    admin = await create_user(app, username="admin", role=UserRole.ADMIN)
    app.state.indexer_adapters = {"torznab": WorkingAdapter()}
    await login(client, admin.username, "correct horse battery staple")
    payload = {
        "name": "example",
        "protocol": "torznab",
        "implementation": "torznab",
        "base_url": "https://indexer.example/",
        "api_key": "secret-value",
        "priority": 4,
    }

    created = await client.post("/api/admin/indexers", json=payload, headers=csrf_headers(client))

    assert created.status_code == 201
    assert "api_key" not in created.text
    indexer_id = created.json()["id"]
    assert created.json()["stats"] == {
        "queries": 0,
        "failures": 0,
        "average_latency_ms": None,
        "grabs": 0,
    }
    async with AsyncSession(app.state.engine) as session:
        indexer = await session.get(Indexer, UUID(indexer_id))
        assert indexer is not None and indexer.api_key == "secret-value"
        assert await session.scalar(text("select api_key from indexers")) != "secret-value"

    tested = await client.post(
        f"/api/admin/indexers/{indexer_id}/test", headers=csrf_headers(client)
    )

    assert tested.status_code == 200
    assert tested.json()["categories"] == [{"id": "5000", "name": "TV"}]
    assert (await client.get("/api/admin/indexers")).json()[0]["health"] == "healthy"
    assert (
        await client.delete(f"/api/admin/indexers/{indexer_id}", headers=csrf_headers(client))
    ).status_code == 204


async def test_indexer_connection_failure_reports_a_redacted_cause(app, client) -> None:
    admin = await create_user(app, username="admin", role=UserRole.ADMIN)
    app.state.indexer_adapters = {"torznab": FailingAdapter()}
    await login(client, admin.username, "correct horse battery staple")
    created = await client.post(
        "/api/admin/indexers",
        json={
            "name": "example",
            "protocol": "torznab",
            "implementation": "torznab",
            "base_url": "https://indexer.example",
            "api_key": "secret-value",
        },
        headers=csrf_headers(client),
    )

    failed = await client.post(
        f"/api/admin/indexers/{created.json()['id']}/test", headers=csrf_headers(client)
    )

    assert failed.status_code == 422
    assert failed.json()["code"] == "INDEXER_CONNECTION_FAILED"
    assert failed.json()["context"]["reason"] == "https://indexer.example rejected [redacted]"
    assert "secret-value" not in failed.text
    stored = (await client.get("/api/admin/indexers")).json()[0]
    assert stored["health"] == "unhealthy"
    assert stored["health_reason"] == "transient"
    assert stored["last_error"] == "https://indexer.example rejected [redacted]"


async def test_admin_can_reset_an_unhealthy_indexer(app, client) -> None:
    admin = await create_user(app, username="admin", role=UserRole.ADMIN)
    await login(client, admin.username, "correct horse battery staple")
    created = await client.post(
        "/api/admin/indexers",
        json={
            "name": "example",
            "protocol": "torznab",
            "implementation": "torznab",
            "base_url": "https://indexer.example",
            "api_key": "secret-value",
        },
        headers=csrf_headers(client),
    )
    indexer_id = created.json()["id"]
    breaker = CircuitBreaker(app.state.redis)
    await app.state.redis.set(breaker.failure_key(indexer_id), "3", ex=300)
    async with AsyncSession(app.state.engine, expire_on_commit=False) as session:
        indexer = await session.get(Indexer, UUID(indexer_id))
        assert indexer is not None
        indexer.health = "unhealthy"
        indexer.last_error = "The indexer search timed out."
        await session.commit()

    reset = await client.post(
        f"/api/admin/indexers/{indexer_id}/reset", headers=csrf_headers(client)
    )

    assert reset.status_code == 200
    assert reset.json()["health"] == "unknown"
    assert reset.json()["health_reason"] is None
    assert reset.json()["last_error"] is None
    assert await app.state.redis.get(breaker.failure_key(indexer_id)) is None


async def test_regular_users_cannot_manage_indexers(app, client) -> None:
    user = await create_user(app)
    await login(client, user.username, "correct horse battery staple")

    response = await client.get("/api/admin/indexers")

    assert response.status_code == 403
