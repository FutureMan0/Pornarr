"""Administrator audit records are durable, private, and queryable."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from pornarr_db.models.audit import AuditLog
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


async def test_admin_mutation_creates_one_private_audit_entry(app, client) -> None:
    admin = await create_user(app, role=UserRole.ADMIN)
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

    assert created.status_code == 201
    response = await client.get("/api/admin/audit", params={"action": "oidc_provider.created"})
    assert response.status_code == 200
    assert response.json()[0]["actor_id"] == str(admin.id)
    assert response.json()[0]["target"] == created.json()["id"]
    assert "secret" not in response.text

    async with AsyncSession(app.state.engine, expire_on_commit=False) as session:
        records = list(await session.scalars(select(AuditLog)))
    assert len(records) == 1
    assert records[0].source == "session"


async def test_audit_log_is_administrator_only(app, client) -> None:
    user = await create_user(app)
    await login(client, user.username, "correct horse battery staple")

    assert (await client.get("/api/admin/audit")).status_code == 403
