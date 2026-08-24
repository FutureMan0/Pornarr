"""Administrator-only account state: `/api/admin/users`.

`get_current_user` has always refused a session whose account is inactive; until
this router there was no way to make one inactive except a SQL `UPDATE`. What is
pinned here is the whole of that: the refusal is administrator-only, it drops the
account's sessions in the same request rather than at their next expiry, it is
audited, and an administrator cannot lock themselves — or the instance — out.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from pornarr_api.auth import SESSION_COOKIE, session_key
from pornarr_db.models.audit import AuditLog
from pornarr_db.models.user import User, UserRole
from tests.api.test_auth import build_app, create_user, csrf_headers, login

PASSWORD = "correct horse battery staple"
MISSING_ID = "00000000-0000-0000-0000-000000000000"


@pytest.fixture
async def app() -> AsyncIterator[FastAPI]:
    application, engine = await build_app()
    yield application
    await engine.dispose()


@asynccontextmanager
async def signed_in(application: FastAPI, username: str) -> AsyncIterator[AsyncClient]:
    async with AsyncClient(
        transport=ASGITransport(app=application), base_url="http://test"
    ) as client:
        await login(client, username, PASSWORD)
        yield client


async def is_active(application: FastAPI, username: str) -> bool:
    async with AsyncSession(application.state.engine) as session:
        user = await session.scalar(select(User).where(User.username == username))
        assert user is not None
        return user.is_active


async def audit_actions(application: FastAPI) -> list[str]:
    async with AsyncSession(application.state.engine) as session:
        return list(await session.scalars(select(AuditLog.action).order_by(AuditLog.created_at)))


async def active_administrators(application: FastAPI) -> int:
    async with AsyncSession(application.state.engine) as session:
        rows = await session.scalars(
            select(User).where(User.role == UserRole.ADMIN, User.is_active.is_(True))
        )
        return len(list(rows))


async def test_only_an_administrator_sees_the_accounts(app: FastAPI) -> None:
    await create_user(app, username="root", role=UserRole.ADMIN)
    await create_user(app, username="alice")

    async with signed_in(app, "alice") as viewer:
        refused = await viewer.get("/api/admin/users")
        assert refused.status_code == 403
        assert refused.json()["code"] == "FORBIDDEN"

    async with signed_in(app, "root") as admin:
        listed = await admin.get("/api/admin/users")

    assert listed.status_code == 200
    assert [(row["username"], row["role"], row["is_active"]) for row in listed.json()] == [
        ("alice", "user", True),
        ("root", "admin", True),
    ]


async def test_a_viewer_cannot_deactivate_anybody(app: FastAPI) -> None:
    root = await create_user(app, username="root", role=UserRole.ADMIN)
    await create_user(app, username="alice")

    async with signed_in(app, "alice") as viewer:
        refused = await viewer.patch(
            f"/api/admin/users/{root.id}", json={"is_active": False}, headers=csrf_headers(viewer)
        )

    assert refused.status_code == 403
    assert refused.json()["code"] == "FORBIDDEN"
    # Refused, and nothing happened.
    assert await is_active(app, "root") is True
    assert await audit_actions(app) == []


async def test_deactivating_an_account_ends_its_sessions_in_the_same_request(app: FastAPI) -> None:
    await create_user(app, username="root", role=UserRole.ADMIN)
    alice = await create_user(app, username="alice")

    async with signed_in(app, "alice") as viewer:
        token = viewer.cookies.get(SESSION_COOKIE)
        assert token is not None
        assert (await viewer.get("/api/auth/me")).status_code == 200

        async with signed_in(app, "root") as admin:
            response = await admin.patch(
                f"/api/admin/users/{alice.id}",
                json={"is_active": False},
                headers=csrf_headers(admin),
            )

        assert response.status_code == 200
        assert response.json()["is_active"] is False
        assert await is_active(app, "alice") is False
        # Immediately, not at the next expiry: the record is gone before the
        # answer is sent, so a held cookie is already worthless.
        assert session_key(token) not in app.state.redis.values
        viewer.cookies.set(SESSION_COOKIE, token, path="/api")
        refused = await viewer.get("/api/auth/me")
        assert refused.status_code == 401
        assert refused.json()["code"] == "NOT_AUTHENTICATED"
        assert await audit_actions(app) == ["user.deactivated"]

        # And it cannot sign in again, so the account is shut out rather than
        # merely signed out.
        signing_in = await viewer.post(
            "/api/auth/login", json={"username": "alice", "password": PASSWORD}
        )
        assert signing_in.status_code == 401
        assert signing_in.json()["code"] == "INVALID_CREDENTIALS"
        assert signing_in.headers.get("set-cookie") is None


async def test_restoring_an_account_lets_it_sign_in_again(app: FastAPI) -> None:
    await create_user(app, username="root", role=UserRole.ADMIN)
    alice = await create_user(app, username="alice")

    async with signed_in(app, "root") as admin:
        for state in (False, True):
            response = await admin.patch(
                f"/api/admin/users/{alice.id}",
                json={"is_active": state},
                headers=csrf_headers(admin),
            )
            assert response.status_code == 200

    assert await is_active(app, "alice") is True
    assert await audit_actions(app) == ["user.deactivated", "user.activated"]
    async with signed_in(app, "alice") as viewer:
        assert (await viewer.get("/api/auth/me")).status_code == 200


async def test_an_administrator_cannot_deactivate_their_own_account(app: FastAPI) -> None:
    root = await create_user(app, username="root", role=UserRole.ADMIN)
    await create_user(app, username="second", role=UserRole.ADMIN)

    async with signed_in(app, "root") as admin:
        token = admin.cookies.get(SESSION_COOKIE)
        response = await admin.patch(
            f"/api/admin/users/{root.id}", json={"is_active": False}, headers=csrf_headers(admin)
        )

        assert response.status_code == 409
        assert response.json()["code"] == "USER_CANNOT_DEACTIVATE_SELF"
        # Refused, and nothing happened: still active, still signed in, and no
        # audit record for something that did not occur.
        assert await is_active(app, "root") is True
        assert token is not None
        assert session_key(token) in app.state.redis.values
        assert (await admin.get("/api/auth/me")).status_code == 200
        assert await audit_actions(app) == []


async def test_an_administrator_always_remains(app: FastAPI) -> None:
    """The self-refusal is also the last-administrator guarantee.

    The caller has passed `require_role(ADMIN)`, so the caller is an active
    administrator; refusing only the caller's own row is therefore enough to keep
    at least one. Asserted as the invariant rather than as a second error code,
    because a "last administrator" branch is one no single request can reach --
    see the module docstring of `routers/admin_users.py`.
    """
    root = await create_user(app, username="root", role=UserRole.ADMIN)
    second = await create_user(app, username="second", role=UserRole.ADMIN)

    async with signed_in(app, "root") as admin:
        assert (
            await admin.patch(
                f"/api/admin/users/{second.id}",
                json={"is_active": False},
                headers=csrf_headers(admin),
            )
        ).status_code == 200
        assert await active_administrators(app) == 1

        # `root` is now the only one, and is refused its own row.
        last = await admin.patch(
            f"/api/admin/users/{root.id}", json={"is_active": False}, headers=csrf_headers(admin)
        )
        assert last.status_code == 409
        assert last.json()["code"] == "USER_CANNOT_DEACTIVATE_SELF"

    assert await active_administrators(app) == 1


async def test_an_unknown_account_is_a_404_and_creates_nothing(app: FastAPI) -> None:
    await create_user(app, username="root", role=UserRole.ADMIN)

    async with signed_in(app, "root") as admin:
        response = await admin.patch(
            f"/api/admin/users/{MISSING_ID}", json={"is_active": False}, headers=csrf_headers(admin)
        )

    assert response.status_code == 404
    assert await audit_actions(app) == []
    async with AsyncSession(app.state.engine) as session:
        assert len(list(await session.scalars(select(User)))) == 1
