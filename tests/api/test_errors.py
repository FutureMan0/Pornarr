from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator
from io import StringIO

import pytest
from fastapi import APIRouter, FastAPI, HTTPException
from httpx import ASGITransport, AsyncClient
from pydantic import BaseModel

from pornarr_api.main import create_app
from pornarr_api.middleware import current_request_id
from pornarr_shared.errors import PornarrError
from pornarr_shared.logging import JsonFormatter
from tests.api.test_app import build_settings


class Payload(BaseModel):
    name: str
    count: int


class TeapotError(PornarrError):
    code = "I_AM_A_TEAPOT"
    status = 418


def _app_with_failing_routes() -> FastAPI:
    router = APIRouter(prefix="/api")

    @router.get("/domain-error")
    async def domain_error() -> None:
        raise TeapotError("short and stout", vessel="teapot")

    @router.get("/http-error")
    async def http_error() -> None:
        raise HTTPException(status_code=403)

    @router.get("/unexpected")
    async def unexpected() -> None:
        raise ValueError("connection to postgres://user:hunter2@db failed")

    @router.post("/validated")
    async def validated(payload: Payload) -> Payload:
        return payload

    app = create_app(build_settings())
    app.include_router(router)
    return app


@pytest.fixture
async def client() -> AsyncIterator[AsyncClient]:
    app = _app_with_failing_routes()
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        yield client


async def test_domain_error_keeps_its_code_status_and_context(client: AsyncClient) -> None:
    response = await client.get("/api/domain-error")

    assert response.status_code == 418
    body = response.json()
    assert body["code"] == "I_AM_A_TEAPOT"
    assert body["context"] == {"vessel": "teapot"}


async def test_http_exception_is_mapped_to_a_stable_code(client: AsyncClient) -> None:
    response = await client.get("/api/http-error")

    assert response.status_code == 403
    assert response.json()["code"] == "FORBIDDEN"


async def test_unexpected_error_never_leaks_the_exception_text() -> None:
    """An exception message can contain a connection string, a path or a bound
    parameter. It belongs in the log, not in the response."""
    app = _app_with_failing_routes()
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/unexpected")

    assert response.status_code == 500
    assert response.json() == {"code": "INTERNAL_ERROR", "status": 500, "context": {}}
    assert "hunter2" not in response.text
    assert "postgres" not in response.text


async def test_validation_error_reports_fields_but_not_values(client: AsyncClient) -> None:
    """Echoing the submitted value into an error is how a password reaches a log
    aggregator by way of the client."""
    response = await client.post("/api/validated", json={"name": "x", "count": "not-a-number"})

    assert response.status_code == 422
    body = response.json()
    assert body["code"] == "VALIDATION_FAILED"
    assert body["context"]["fields"][0]["location"] == ["body", "count"]
    assert "not-a-number" not in response.text


async def test_a_route_registered_after_create_app_is_reachable(client: AsyncClient) -> None:
    """The SPA fallback used to be a catch-all route, which shadowed every router
    added after the factory. It is a 404 handler now, so ordering cannot bite."""
    response = await client.get("/api/http-error")
    assert response.status_code != 200
    assert response.headers["content-type"].startswith("application/json")


async def test_request_completion_log_carries_the_request_identifier(client: AsyncClient) -> None:
    stream = StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonFormatter())
    request_logger = logging.getLogger("pornarr_api.middleware")
    previous_level = request_logger.level
    previous_propagate = request_logger.propagate
    request_logger.addHandler(handler)
    request_logger.setLevel(logging.INFO)
    request_logger.propagate = False
    try:
        response = await client.get("/api/http-error", headers={"X-Request-Id": "trace-123"})
    finally:
        request_logger.removeHandler(handler)
        request_logger.setLevel(previous_level)
        request_logger.propagate = previous_propagate

    assert response.headers["X-Request-Id"] == "trace-123"
    event = json.loads(stream.getvalue())
    assert event["request_id"] == "trace-123"
    assert event["message"] == "request completed: GET /api/http-error 403"


async def test_valid_payload_still_works(client: AsyncClient) -> None:
    response = await client.post("/api/validated", json={"name": "x", "count": 3})

    assert response.status_code == 200
    assert response.json() == {"name": "x", "count": 3}


def test_request_id_outside_a_request_is_a_placeholder() -> None:
    """Worker code shares the logging setup and has no request to belong to."""
    assert current_request_id() == "-"
