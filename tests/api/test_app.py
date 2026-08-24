from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr

from pornarr_api.main import create_app
from pornarr_api.middleware import REQUEST_ID_HEADER
from pornarr_shared.config import Settings

SECRET = "0123456789abcdef0123456789abcdef"


def build_settings(base_path: str = "") -> Settings:
    """Settings for the HTTP suite.

    `session_cookie_secure` is stated rather than left to its default because
    the suite drives the app over `http://test`, and a cookie jar does not
    return a `Secure` cookie to a plain-HTTP request. The default is `True`, so
    every authenticated case here held only on a machine whose `.env` sets
    `SESSION_COOKIE_SECURE=false` for local HTTP -- which a developer machine
    has and a runner has not. Left to the host, 243 of them answered 401 or
    CSRF_FAILED on CI and nowhere else.
    """
    return Settings(
        app_secret=SecretStr(SECRET),
        database_url="postgresql+psycopg://pornarr:pornarr@localhost:5432/pornarr",
        redis_url="redis://localhost:6379/0",
        base_path=base_path,
        app_env="test",
        session_cookie_secure=False,
    )


@pytest.fixture
def static_root(tmp_path: Path) -> Path:
    """A built frontend, as the Dockerfile would produce."""
    (tmp_path / "assets").mkdir()
    (tmp_path / "index.html").write_text("<!doctype html><title>Pornarr</title>")
    return tmp_path


@pytest.fixture
def client(static_root: Path) -> Iterator[AsyncClient]:
    """A client that does not run lifespan, so no database or Redis is needed."""
    app = create_app(build_settings(), static_root=static_root)
    yield AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def test_unknown_api_path_returns_a_structured_error_not_html(
    client: AsyncClient,
) -> None:
    """A JSON client that receives index.html gets a parse error instead of a 404."""
    response = await client.get("/api/does-not-exist")

    assert response.status_code == 404
    assert response.headers["content-type"].startswith("application/json")
    assert response.json()["code"] == "NOT_FOUND"


async def test_unknown_client_route_returns_the_spa(client: AsyncClient) -> None:
    response = await client.get("/library/some/deep/route")

    assert response.status_code == 200
    assert "<title>Pornarr</title>" in response.text


async def test_every_response_carries_a_request_id(client: AsyncClient) -> None:
    response = await client.get("/api/does-not-exist")

    assert response.headers[REQUEST_ID_HEADER]


async def test_an_inbound_request_id_is_adopted(client: AsyncClient) -> None:
    """Adopting the proxy's identifier is what makes a trace span the proxy and
    the API rather than restarting at our edge."""
    response = await client.get("/api/x", headers={REQUEST_ID_HEADER: "from-the-proxy"})

    assert response.headers[REQUEST_ID_HEADER] == "from-the-proxy"


async def test_an_absurdly_long_inbound_id_is_replaced(client: AsyncClient) -> None:
    response = await client.get("/api/x", headers={REQUEST_ID_HEADER: "x" * 500})

    assert response.headers[REQUEST_ID_HEADER] != "x" * 500


async def test_request_ids_differ_between_requests(client: AsyncClient) -> None:
    first = await client.get("/api/x")
    second = await client.get("/api/x")

    assert first.headers[REQUEST_ID_HEADER] != second.headers[REQUEST_ID_HEADER]


async def test_missing_frontend_build_says_so_rather_than_404(tmp_path: Path) -> None:
    """A 503 naming the expected path beats a 404 that looks like a routing bug."""
    app = create_app(build_settings(), static_root=tmp_path / "never-built")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as bare_client:
        response = await bare_client.get("/")

    assert response.status_code == 503
    assert response.json()["code"] == "WEB_ASSETS_MISSING"


async def test_base_path_is_applied_for_sub_path_deployment(static_root: Path) -> None:
    app = create_app(build_settings(base_path="/pornarr"), static_root=static_root)

    assert app.root_path == "/pornarr"


async def test_docs_are_disabled_in_production(static_root: Path) -> None:
    """Interactive docs on a public deployment enumerate the whole API surface."""
    settings = build_settings().model_copy(update={"app_env": "production"})
    app = create_app(settings, static_root=static_root)

    assert app.docs_url is None


async def test_openapi_schema_is_served_under_the_api_prefix(client: AsyncClient) -> None:
    response = await client.get("/api/openapi.json")

    assert response.status_code == 200
    assert response.json()["info"]["title"] == "Pornarr"


@pytest.fixture(autouse=True)
async def _close_client(client: AsyncClient) -> AsyncIterator[None]:
    yield
    await client.aclose()
