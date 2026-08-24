"""Local-account authentication behaviour.

The fast suite uses SQLite and an in-memory Redis double only to exercise the
HTTP behaviour. The matching integration test covers PostgreSQL and Redis, the
two services the application actually supports.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from fastapi import APIRouter, Depends, FastAPI
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr, ValidationError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine

import pornarr_api.auth as auth_module
from pornarr_api.auth import CSRF_COOKIE, CSRF_HEADER, SESSION_COOKIE, hash_password, require_role
from pornarr_api.main import create_app
from pornarr_db.base import Base
from pornarr_db.models.user import User, UserRole
from pornarr_shared.config import Settings
from tests.api.test_app import SECRET, build_settings


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


BOOTSTRAP_VARIABLES = ("APP_ENV", "SESSION_COOKIE_SECURE", "TRUSTED_PROXIES")


def settings_from_environment(monkeypatch: pytest.MonkeyPatch, **variables: str) -> Settings:
    """Settings built from a chosen environment and nothing else.

    `.env` is ignored and the three variables below are cleared first, because a
    developer's `.env` sets `SESSION_COOKIE_SECURE=false` so a browser accepts the
    cookie over plain HTTP, and a test that asserts a default must not read the
    answer out of the machine it happens to run on. Values are the strings an
    operator writes, so the coercion is exercised too.
    """
    for name in BOOTSTRAP_VARIABLES:
        monkeypatch.delenv(name, raising=False)
    for name, value in variables.items():
        monkeypatch.setenv(name, value)
    return Settings(
        _env_file=None,
        app_secret=SecretStr(SECRET),
        database_url="postgresql+psycopg://pornarr:pornarr@localhost:5432/pornarr",
        redis_url="redis://localhost:6379/0",
    )


async def build_app(
    settings: Settings | None = None, static_root: Path | None = None
) -> tuple[FastAPI, AsyncEngine]:
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    application = create_app(settings or build_settings(), static_root=static_root)
    application.state.engine = engine
    application.state.redis = MemoryRedis()
    application.state.queue = MemoryQueue()
    return application, engine


@pytest.fixture
async def app() -> AsyncIterator[FastAPI]:
    application, engine = await build_app()
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


async def test_an_unproven_api_key_header_does_not_switch_off_csrf(
    app: FastAPI, client: AsyncClient
) -> None:
    """api-contract.md L62-63 exempts API-key requests because they carry no
    ambient credential. A request holding a session cookie carries one, so it has
    not earned the exemption whatever header it also names."""
    user = await create_user(app)
    await login(client, user.username, "correct horse battery staple")

    response = await client.post("/api/auth/logout", headers={"X-Api-Key": "pnr_not-a-real-key"})

    assert response.status_code == 403
    assert response.json()["code"] == "CSRF_FAILED"
    assert (await client.get("/api/auth/me")).status_code == 200


def test_the_session_cookie_is_secure_unless_a_deployment_opts_out(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The default is the TLS one, and the opt-out is refused in production.

    `_cookie_is_secure` used to read `app_env == "production"` while
    `.env.example` -- the file `make setup` writes -- ships
    `APP_ENV=development`, so an operator who followed installation.md to a TLS
    reverse proxy served the session cookie without `Secure` and one plain-HTTP
    request handed it to anybody on the path.
    """
    # The shape `.env.example` and installation.md produce, and the one an
    # operator who sets nothing at all gets.
    assert settings_from_environment(monkeypatch).session_cookie_secure is True
    assert (
        settings_from_environment(monkeypatch, APP_ENV="development").session_cookie_secure is True
    )
    assert (
        settings_from_environment(monkeypatch, APP_ENV="production").session_cookie_secure is True
    )
    assert (
        settings_from_environment(monkeypatch, SESSION_COOKIE_SECURE="false").session_cookie_secure
        is False
    )

    with pytest.raises(ValidationError) as refused:
        settings_from_environment(monkeypatch, APP_ENV="production", SESSION_COOKIE_SECURE="false")

    assert "SESSION_COOKIE_SECURE" in str(refused.value)


@pytest.mark.parametrize(("configured", "secure"), [("true", True), ("false", False)])
async def test_login_marks_both_cookies_secure_when_the_setting_says_so(
    monkeypatch: pytest.MonkeyPatch, configured: str, secure: bool
) -> None:
    application, engine = await build_app(
        settings_from_environment(monkeypatch, APP_ENV="test", SESSION_COOKIE_SECURE=configured)
    )
    try:
        user = await create_user(application)
        async with AsyncClient(
            transport=ASGITransport(app=application), base_url="http://test"
        ) as client:
            response = await client.post(
                "/api/auth/login",
                json={"username": user.username, "password": "correct horse battery staple"},
            )

            assert response.status_code == 200
            cookies = response.headers.get_list("set-cookie")
            assert len(cookies) == 2
            assert all(("Secure" in cookie) is secure for cookie in cookies), cookies
    finally:
        await engine.dispose()


def test_trusted_proxies_must_be_addresses_or_blocks(monkeypatch: pytest.MonkeyPatch) -> None:
    # Empty by default: believe nobody.
    assert settings_from_environment(monkeypatch).trusted_proxy_networks == ()
    parsed = settings_from_environment(
        monkeypatch, TRUSTED_PROXIES=" 127.0.0.1 , 172.18.0.0/16 "
    ).trusted_proxy_networks
    assert [str(network) for network in parsed] == ["127.0.0.1/32", "172.18.0.0/16"]

    with pytest.raises(ValidationError) as refused:
        settings_from_environment(monkeypatch, TRUSTED_PROXIES="proxy.example.test")

    assert "TRUSTED_PROXIES" in str(refused.value)


async def failed_login(client: AsyncClient, username: str, forwarded: str | None = None) -> int:
    headers = {"X-Forwarded-For": forwarded} if forwarded is not None else {}
    response = await client.post(
        "/api/auth/login",
        json={"username": username, "password": "wrong"},
        headers=headers,
    )
    return response.status_code


async def test_the_login_limit_counts_each_client_behind_a_trusted_proxy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Behind the documented reverse proxy the peer is the proxy for everybody.

    `ASGITransport` presents 127.0.0.1 as the peer, so naming it as the trusted
    proxy is the same shape as nginx reaching the API on the loopback address.
    Six wrong guesses from one forwarded client must fill that client's bucket
    and nobody else's.
    """
    application, engine = await build_app(
        settings_from_environment(monkeypatch, APP_ENV="test", TRUSTED_PROXIES="127.0.0.1")
    )
    try:
        # SetupMiddleware answers 503 until an account exists, and this file's
        # `app` fixture leaves that to each test.
        await create_user(application, username="resident")
        async with AsyncClient(
            transport=ASGITransport(app=application), base_url="http://test"
        ) as client:
            attacker = [
                await failed_login(client, f"ghost-{index}", "203.0.113.7") for index in range(6)
            ]
            assert attacker == [401, 401, 401, 401, 401, 429]

            # Same forwarded client, a name it has not tried: still refused, so
            # the address bucket is what filled up and not one account's.
            assert await failed_login(client, "ghost-elsewhere", "203.0.113.7") == 429
            # A different forwarded client, from the same proxy: unaffected.
            assert await failed_login(client, "ghost-elsewhere", "198.51.100.4") == 401
            # And the hops the proxy itself added are skipped, so a client that
            # writes its own header cannot hide behind the trusted one.
            assert await failed_login(client, "ghost-chained", "198.51.100.9, 127.0.0.1") == 401
            assert await failed_login(client, "ghost-chained", "203.0.113.7, 127.0.0.1") == 429
    finally:
        await engine.dispose()


async def test_a_forwarded_header_from_an_untrusted_client_cannot_split_the_bucket(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The default trusts nobody, so a forged header must change nothing.

    This is the other half of the fix and the more important one: an instance
    that believed `X-Forwarded-For` from any caller would let an attacker put
    every guess in a bucket of its own, and the login rate limit would stop
    existing.
    """
    application, engine = await build_app(settings_from_environment(monkeypatch, APP_ENV="test"))
    try:
        await create_user(application, username="resident")
        assert application.state.settings.trusted_proxies == ""
        async with AsyncClient(
            transport=ASGITransport(app=application), base_url="http://test"
        ) as client:
            statuses = [
                await failed_login(client, f"ghost-{index}", f"203.0.113.{index + 1}")
                for index in range(6)
            ]

            assert statuses == [401, 401, 401, 401, 401, 429]
            counters = [
                key
                for key in application.state.redis.values
                if key.startswith("pornarr:auth:login:ip:")
            ]
            assert len(counters) == 1, counters
            assert await failed_login(client, "ghost-fresh", "198.51.100.4") == 429
    finally:
        await engine.dispose()


async def test_a_password_is_verified_even_for_a_username_nobody_has(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Enumeration by stopwatch: an unknown name answered in 9ms, a known one 56ms.

    The timing itself is asserted end to end, over a distribution, in
    `tests/e2e/auth.spec.ts`. What is pinned here is the mechanism that makes the
    two equal -- that the Argon2 verification runs whether or not the row exists,
    and against a real encoded hash rather than `PASSWORDLESS_PASSWORD_HASH`,
    which raises `InvalidHashError` before deriving anything and costs nothing.
    """
    verified: list[str] = []
    real = auth_module.verify_password

    def record(password_hash: str, password: str) -> bool:
        verified.append(password_hash)
        return real(password_hash, password)

    monkeypatch.setattr(auth_module, "verify_password", record)
    known = await create_user(app, username="present")
    await create_user(app, username="passwordless", password="unused")
    async with AsyncSession(app.state.engine) as session:
        row = await session.get(User, (await create_user(app, username="oidc-only")).id)
        assert row is not None
        row.password_hash = auth_module.PASSWORDLESS_PASSWORD_HASH
        await session.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        for username in ("nobody-at-all", known.username, "oidc-only"):
            assert await failed_login(client, username) == 401

    assert len(verified) == 3
    assert verified[0] == auth_module._ABSENT_PASSWORD_HASH
    assert verified[1].startswith("$argon2id$") and verified[1] != auth_module._ABSENT_PASSWORD_HASH
    # The account with no local password is charged the same work, so it is not
    # distinguishable from a name nobody has either.
    assert verified[2] == auth_module._ABSENT_PASSWORD_HASH
    assert auth_module._ABSENT_PASSWORD_HASH.startswith("$argon2id$")


def test_the_contract_declares_how_to_authenticate(app: FastAPI) -> None:
    """Contradiction C3: the document declared no security scheme at all.

    ADR 0009 makes `openapi.json` the boundary the TypeScript client and the MSW
    handlers are generated from, and api-contract.md names three authentication
    paths, so a client generated strictly from the document could not know that
    anything on the instance was protected.
    """
    document = app.openapi()
    schemes = document["components"]["securitySchemes"]

    assert schemes["SessionCookie"]["in"] == "cookie"
    assert schemes["SessionCookie"]["name"] == SESSION_COOKIE
    assert schemes["ApiKey"] == {**schemes["ApiKey"], "in": "header", "name": "X-Api-Key"}

    both = [{"SessionCookie": []}, {"ApiKey": []}]
    assert document["paths"]["/api/library"]["get"]["security"] == both
    assert document["paths"]["/api/admin/users/{user_id}"]["patch"]["security"] == both
    # `/api/auth/*` refuses API keys, and says so rather than offering both.
    assert document["paths"]["/api/auth/me"]["get"]["security"] == [{"SessionCookie": []}]
    # And the genuinely anonymous operations carry none, so the document is a
    # description of the product rather than a blanket assertion.
    unsecured = sorted(
        path
        for path, methods in document["paths"].items()
        for operation in methods.values()
        if "security" not in operation
    )
    assert unsecured == [
        "/api/auth/login",
        "/api/auth/oidc/callback",
        "/api/auth/oidc/providers",
        "/api/auth/oidc/{provider_id}/login",
        "/api/health",
        "/api/invites/{token}",
        "/api/invites/{token}/redeem",
        "/api/setup/complete",
        "/api/setup/status",
        "/api/setup/test-download-client",
        "/api/setup/test-indexer",
        "/api/setup/validate-library-path",
    ]
