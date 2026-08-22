"""When a request's transaction commits, relative to when its response is sent.

A caller that writes and then immediately reads is the normal shape of this
product's frontend: every mutation is followed by a refetch. If the commit
happens after the response leaves, that refetch is a second request racing a
transaction that has not landed yet, and it wins often enough to be seen --
`POST /api/admin/download-clients` answering 201 and the test of the client it
just created answering 404 fourteen milliseconds later, `POST /api/requests`
answering 201 and the list that follows coming back without the row.

The two tests below pin the two halves of the fix, and both observe from inside
the task that ran the route (see `AnswerProbe`) so that neither of them is a
race the machine happens to win.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from pathlib import Path

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.pool import QueuePool
from starlette.middleware import Middleware
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from pornarr_api.main import create_app
from pornarr_db.base import Base
from tests.api.test_app import build_settings
from tests.api.test_auth import MemoryQueue, MemoryRedis, create_user, csrf_headers, login
from tests.api.test_stream import add_root_folder, create_media_file

PASSWORD = "correct horse battery staple"


class AnswerProbe:
    """One callback, run at the instant a route hands its response to the client."""

    def __init__(self) -> None:
        self._pending: Callable[[], Awaitable[None]] | None = None

    def arm(self, callback: Callable[[], Awaitable[None]]) -> None:
        self._pending = callback

    async def fire(self) -> None:
        # Cleared before it runs, so the requests the callback itself makes do
        # not re-enter it.
        callback, self._pending = self._pending, None
        if callback is not None:
            await callback()


class AnswerProbeMiddleware:
    """Fires an `AnswerProbe` when the response status line goes out.

    Appended to `user_middleware` instead of added with `add_middleware`, which
    prepends: the list runs outermost first, so appending puts this *inside*
    `RequestIdMiddleware` and `SetupMiddleware`. That placement is the whole
    point. Both of those are `BaseHTTPMiddleware`, which relays a response
    between two tasks through a zero-buffer stream, so a probe outside them
    would watch the response travelling in one task while the route's cleanup
    ran in another -- an ordering no test can assert without flipping a coin.
    Inside them, `send` is called by the endpoint's own task, after FastAPI has
    built the response and before it unwinds the request-scoped dependencies,
    which is exactly the window this file is about.
    """

    def __init__(self, app: ASGIApp, probe: AnswerProbe) -> None:
        self.app = app
        self.probe = probe

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        async def watched(message: Message) -> None:
            await send(message)
            if message["type"] == "http.response.start":
                await self.probe.fire()

        await self.app(scope, receive, watched)


def borrowed_connections(engine: AsyncEngine) -> int:
    """How many of the pool's connections are checked out right now.

    Only a queueing pool counts what it has lent out, which is the one a
    file-backed database gets; the assertion says so rather than letting a
    future change to the fixture turn the count into an attribute error.
    """
    pool = engine.pool
    assert isinstance(pool, QueuePool)
    return pool.checkedout()


@pytest.fixture
def probe() -> AnswerProbe:
    return AnswerProbe()


@pytest.fixture
async def engine(tmp_path: Path) -> AsyncIterator[AsyncEngine]:
    """A database in a file, not the `sqlite+aiosqlite://` the other suites use.

    The in-memory database is one connection shared by every session
    (`StaticPool`), so an uncommitted write is visible to every reader and the
    isolation these tests observe does not exist there. A file gives each
    checkout its own connection, which is what a deployment has.
    """
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'pornarr.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest.fixture
async def app(tmp_path: Path, engine: AsyncEngine, probe: AnswerProbe) -> AsyncIterator[FastAPI]:
    settings = build_settings().model_copy(update={"data_path": tmp_path})
    settings.library_path.mkdir()
    application = create_app(settings)
    application.state.engine = engine
    application.state.redis = MemoryRedis()
    application.state.queue = MemoryQueue()
    application.user_middleware.append(Middleware(AnswerProbeMiddleware, probe=probe))
    yield application


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[AsyncClient]:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http_client:
        yield http_client


async def test_a_written_row_is_readable_by_the_time_the_client_is_answered(
    app: FastAPI, client: AsyncClient, probe: AnswerProbe
) -> None:
    user = await create_user(app)
    await login(client, user.username, PASSWORD)
    read_back: list[list[dict[str, object]]] = []

    async def list_the_requests() -> None:
        read_back.append((await client.get("/api/requests")).json())

    probe.arm(list_the_requests)
    created = await client.post(
        "/api/requests", json={"query": "Example"}, headers=csrf_headers(client)
    )

    assert created.status_code == 201
    # The read is a separate request on a separate connection, issued while the
    # answer to the write is on its way out. It has to see the row: that is the
    # moment the caller's own refetch is free to start.
    assert [row["id"] for row in read_back[0]] == [created.json()["id"]]


async def test_a_stream_holds_no_pooled_connection_while_its_body_is_sent(
    app: FastAPI, client: AsyncClient, engine: AsyncEngine, probe: AnswerProbe
) -> None:
    """The guarantee `streaming_session` exists for, checked at the point it matters.

    A `StreamingResponse` body does not finish while the client stays connected,
    so anything a streaming route still holds when it returns is held for the
    whole life of the stream. The pool is 5 plus 10 overflow, and fifteen open
    event streams once exhausted it and made every other route answer 500.
    """
    path = app.state.settings.library_path / "sample.mp4"
    path.write_bytes(b"abcdefghijklmnopqrstuvwxyz")
    user = await create_user(app)
    media_file = await create_media_file(app, path)
    await add_root_folder(app, app.state.settings.library_path)
    await login(client, user.username, PASSWORD)
    checked_out: list[int] = []

    async def count_borrowed_connections() -> None:
        checked_out.append(borrowed_connections(engine))

    probe.arm(count_borrowed_connections)
    streamed = await client.get(f"/api/media/{media_file.media_id}/stream")

    assert streamed.status_code == 200
    assert streamed.content == b"abcdefghijklmnopqrstuvwxyz"
    # Nothing borrowed at the moment the body starts flowing, so the stream can
    # last as long as the viewer does without costing the pool a connection.
    assert checked_out == [0]
