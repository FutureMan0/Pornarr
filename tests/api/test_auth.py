"""Local-account authentication behaviour.

The fast suite uses SQLite and an in-memory Redis double only to exercise the
HTTP behaviour. The matching integration test covers PostgreSQL and Redis, the
two services the application actually supports.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import AsyncIterator

import pytest
from fastapi import APIRouter, Depends, FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from pornarr_api.auth import CSRF_COOKIE, CSRF_HEADER, SESSION_COOKIE, hash_password, require_role
from pornarr_api.main import create_app
from pornarr_db.base import Base
from pornarr_db.models.user import User, UserRole
from tests.api.test_app import build_settings


class MemoryQueue:
    """The job queue, recorded rather than run.

    A separate object from `MemoryRedis` because it is a separate client in the
    application: the session store decodes to `str`, arq needs bytes, and the
    two cannot be one connection. Keeping them apart here means a test that
    exercises an enqueue path has to say so, instead of quietly attaching
    `enqueue_job` to the session client — which is how the API came to call a
    method its Redis object never had.
    """

    def __init__(self) -> None:
        self.jobs: list[tuple[str, tuple[object, ...], dict[str, object]]] = []

    async def enqueue_job(self, function: str, *args: object, **kwargs: object) -> object:
        self.jobs.append((function, args, kwargs))
        return None


class MemoryRedis:
    """The small Redis surface authentication uses in the fast suite."""

    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.members: defaultdict[str, set[str]] = defaultdict(set)
        self.events: list[dict[str, str]] = []

    async def get(self, key: str) -> str | None:
        return self.values.get(key)

    async def getdel(self, key: str) -> str | None:
        return self.values.pop(key, None)

    async def set(self, key: str, value: str, *, ex: int) -> bool:
        self.values[key] = value
        return True

    async def incr(self, key: str) -> int:
        value = int(self.values.get(key, "0")) + 1
        self.values[key] = str(value)
        return value

    async def expire(self, key: str, time: int) -> bool:
        return True

    async def delete(self, *keys: str) -> int:
        deleted = 0
        for key in keys:
            if key in self.values:
                del self.values[key]
                deleted += 1
            if key in self.members:
                del self.members[key]
                deleted += 1
        return deleted

    async def sadd(self, key: str, *values: str) -> int:
        before = len(self.members[key])
        self.members[key].update(values)
        return len(self.members[key]) - before

    async def srem(self, key: str, *values: str) -> int:
        members = self.members[key]
        before = len(members)
        members.difference_update(values)
        return before - len(members)

    async def sscan_iter(self, key: str) -> AsyncIterator[str]:
        for value in tuple(self.members[key]):
            yield value

    async def xadd(self, _: str, fields: dict[str, str]) -> str:
        self.events.append(fields)
        return f"{len(self.events)}-0"

    async def publish(self, _: str, __: str) -> int:
        return 1


@pytest.fixture
async def app() -> AsyncIterator[FastAPI]:
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    application = create_app(build_settings())
    application.state.engine = engine
    application.state.redis = MemoryRedis()
    yield application
    await engine.dispose()


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[AsyncClient]:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http_client:
        yield http_client


async def create_user(
    app: FastAPI,
    *,
    username: str = "alice",
    password: str = "correct horse battery staple",
    role: UserRole = UserRole.USER,
) -> User:
    async with AsyncSession(app.state.engine, expire_on_commit=False) as session:
        user = User(username=username, password_hash=hash_password(password), role=role)
        session.add(user)
        await session.commit()
    return user


def csrf_headers(client: AsyncClient) -> dict[str, str]:
    token = client.cookies.get(CSRF_COOKIE)
    assert token is not None
    return {CSRF_HEADER: token}


async def login(client: AsyncClient, username: str, password: str) -> None:
    response = await client.post(
        "/api/auth/login", json={"username": username, "password": password}
    )
    assert response.status_code == 200


async def test_login_sets_http_only_session_and_survives_a_reload(
    app: FastAPI, client: AsyncClient
) -> None:
    user = await create_user(app)

    response = await client.post(
        "/api/auth/login",
        json={"username": user.username, "password": "correct horse battery staple"},
    )

    assert response.status_code == 200
    assert response.json() == {"id": str(user.id), "username": "alice", "role": "user"}
    session_cookie, csrf_cookie = response.headers.get_list("set-cookie")
    assert f"{SESSION_COOKIE}=" in session_cookie
    assert "HttpOnly" in session_cookie
    assert "SameSite=lax" in session_cookie
    assert "Path=/api" in session_cookie
    assert f"{CSRF_COOKIE}=" in csrf_cookie
    assert "HttpOnly" not in csrf_cookie
    assert "Path=/" in csrf_cookie

    current_user = await client.get("/api/auth/me")
    assert current_user.status_code == 200
    assert current_user.json() == response.json()


async def test_logout_revokes_the_server_side_session(app: FastAPI, client: AsyncClient) -> None:
    user = await create_user(app)
    await login(client, user.username, "correct horse battery staple")
    session_token = client.cookies.get(SESSION_COOKIE)
    assert session_token is not None

    response = await client.post("/api/auth/logout", headers=csrf_headers(client))

    assert response.status_code == 204
    session_cookie, csrf_cookie = response.headers.get_list("set-cookie")
    assert "Path=/api" in session_cookie
    assert "Path=/" in csrf_cookie
    client.cookies.set(SESSION_COOKIE, session_token, path="/api")
    assert (await client.get("/api/auth/me")).status_code == 401


async def test_logout_everywhere_revokes_every_session(app: FastAPI, client: AsyncClient) -> None:
    user = await create_user(app)
    await login(client, user.username, "correct horse battery staple")

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as second_client:
        await login(second_client, user.username, "correct horse battery staple")
        second_session = second_client.cookies.get(SESSION_COOKIE)
        assert second_session is not None

        response = await client.post("/api/auth/logout-everywhere", headers=csrf_headers(client))

        assert response.status_code == 204
        second_client.cookies.set(SESSION_COOKIE, second_session, path="/api")
        assert (await second_client.get("/api/auth/me")).status_code == 401


async def test_mutating_request_without_the_csrf_token_is_rejected(
    app: FastAPI, client: AsyncClient
) -> None:
    user = await create_user(app)
    await login(client, user.username, "correct horse battery staple")

    response = await client.post("/api/auth/logout")

    assert response.status_code == 403
    assert response.json()["code"] == "CSRF_FAILED"
    assert (await client.get("/api/auth/me")).status_code == 200


async def test_six_failed_logins_are_rate_limited(app: FastAPI, client: AsyncClient) -> None:
    await create_user(app)
    responses = [
        await client.post("/api/auth/login", json={"username": "unknown", "password": "wrong"})
        for _ in range(6)
    ]

    assert [response.status_code for response in responses] == [401, 401, 401, 401, 401, 429]
    assert responses[-1].json()["code"] == "LOGIN_RATE_LIMITED"


async def test_deactivating_a_user_invalidates_existing_sessions(
    app: FastAPI, client: AsyncClient
) -> None:
    user = await create_user(app)
    await login(client, user.username, "correct horse battery staple")

    async with AsyncSession(app.state.engine) as session:
        stored = await session.get(User, user.id)
        assert stored is not None
        stored.is_active = False
        await session.commit()

    response = await client.get("/api/auth/me")
    assert response.status_code == 401


async def test_role_dependency_blocks_regular_users_and_allows_admins(
    app: FastAPI, client: AsyncClient
) -> None:
    router = APIRouter(prefix="/api")

    @router.get("/admin", dependencies=[Depends(require_role(UserRole.ADMIN))])
    async def admin_only() -> dict[str, bool]:
        return {"admin": True}

    app.include_router(router)
    regular_user = await create_user(app)
    admin = await create_user(app, username="root", role=UserRole.ADMIN)
    await login(client, regular_user.username, "correct horse battery staple")

    forbidden = await client.get("/api/admin")
    assert forbidden.status_code == 403

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as admin_client:
        await login(admin_client, admin.username, "correct horse battery staple")
        assert (await admin_client.get("/api/admin")).json() == {"admin": True}


async def test_wrong_password_returns_no_account_detail(app: FastAPI, client: AsyncClient) -> None:
    user = await create_user(app)

    response = await client.post(
        "/api/auth/login", json={"username": user.username, "password": "wrong"}
    )

    assert response.status_code == 401
    assert response.json()["code"] == "INVALID_CREDENTIALS"
    assert user.username not in response.text


def test_auth_contract_declares_structured_error_responses(app: FastAPI) -> None:
    responses = app.openapi()["paths"]["/api/auth/login"]["post"]["responses"]

    assert {"401", "422", "429"} <= responses.keys()
    assert responses["401"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/ErrorResponse"
    }
