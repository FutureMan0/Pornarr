"""A server-sent event stream must let go when the process is shutting down.

uvicorn's graceful shutdown waits for open connections. `/api/events` held one
open for as long as the browser wanted it, so a single idle tab was enough to
make SIGTERM hang until the container's kill timeout — a forced kill that drops
whatever else is in flight rather than draining it. In development with
`--reload` the same thing made every code change hang until the tab was closed.

These are unit tests over the loop rather than an end-to-end SSE request: what
is being pinned down is *when the generator stops*, and driving that through a
real ASGI transport would mean asserting on a timeout, which is the one thing a
test of shutdown promptness must not do.
"""

from __future__ import annotations

import asyncio
from typing import cast

import pytest
from fastapi import Request

from pornarr_api.routers import events

# Far longer than the test should ever wait. If shutdown is honoured only
# between calls to `get_message`, waiting this long is exactly what happens.
BLOCKING_SECONDS = 30

# Generous next to a cancelled await, tight next to BLOCKING_SECONDS.
PROMPTLY = 2.0


class BlockingPubSub:
    """A subscription that never produces a message."""

    def __init__(self) -> None:
        self.unsubscribed = False
        self.closed = False

    async def subscribe(self, *channels: str) -> None:
        return None

    async def get_message(self, *, ignore_subscribe_messages: bool, timeout: float) -> None:
        await asyncio.sleep(BLOCKING_SECONDS)
        return None

    async def unsubscribe(self, *channels: str) -> None:
        self.unsubscribed = True

    async def aclose(self) -> None:
        self.closed = True


class FakeRedis:
    def __init__(self, pubsub: BlockingPubSub) -> None:
        self._pubsub = pubsub

    def pubsub(self) -> BlockingPubSub:
        return self._pubsub


class FakeState:
    def __init__(self, redis: FakeRedis, shutting_down: asyncio.Event | None) -> None:
        self.redis = redis
        if shutting_down is not None:
            self.shutting_down = shutting_down


class FakeApp:
    def __init__(self, state: FakeState) -> None:
        self.state = state


class FakeRequest:
    """Only the surface `_stream` touches."""

    def __init__(self, app: FakeApp) -> None:
        self.app = app
        self.state = app.state
        self.headers: dict[str, str] = {}

    async def is_disconnected(self) -> bool:
        return False


def build_request(shutting_down: asyncio.Event | None) -> tuple[Request, BlockingPubSub]:
    """A stand-in carrying only what `_stream` reads: `app.state` and disconnection.

    Cast rather than constructed: a real `Request` needs an ASGI scope, a receive
    channel and a live app, and building one here would test Starlette.
    """
    pubsub = BlockingPubSub()
    state = FakeState(FakeRedis(pubsub), shutting_down)
    return cast(Request, FakeRequest(FakeApp(state))), pubsub


async def test_a_blocked_read_gives_up_as_soon_as_shutdown_begins() -> None:
    shutting_down = asyncio.Event()
    _, pubsub = build_request(shutting_down)

    async def begin_shutdown() -> None:
        # Long enough that the read is genuinely waiting, short enough that a
        # failure is a failure rather than a slow pass.
        await asyncio.sleep(0.05)
        shutting_down.set()

    async with asyncio.timeout(PROMPTLY):
        _, message = await asyncio.gather(
            begin_shutdown(),
            events._next_message(pubsub, shutting_down),
        )

    # None means "nothing to send": the caller tells a heartbeat apart from a
    # shutdown by re-reading the flag.
    assert message is None


async def test_a_stream_ends_on_shutdown_instead_of_waiting_for_the_browser() -> None:
    shutting_down = asyncio.Event()
    request, pubsub = build_request(shutting_down)

    stream = events._stream(request, "u-1", None)

    async def begin_shutdown() -> None:
        await asyncio.sleep(0.05)
        shutting_down.set()

    async def drain() -> list[str]:
        return [frame async for frame in stream]

    async with asyncio.timeout(PROMPTLY):
        _, frames = await asyncio.gather(begin_shutdown(), drain())

    # It stops rather than emitting a farewell heartbeat: the browser reconnects
    # on its own, and a frame written into a closing connection is a frame that
    # may never arrive.
    assert frames == []
    # And it hands the subscription back rather than leaking it into the pool.
    assert pubsub.unsubscribed is True
    assert pubsub.closed is True


async def test_without_the_flag_the_stream_still_works() -> None:
    """An app that never set `shutting_down` must not crash on a missing attribute.

    The tests in this repo build apps by hand, and so does anything embedding the
    API; reading the flag with `getattr` is what keeps those working.
    """
    request, pubsub = build_request(None)

    stream = events._stream(request, "u-1", None)
    read = asyncio.ensure_future(anext(stream))

    # It blocks on the read rather than raising: no flag, no early exit.
    done, pending = await asyncio.wait({read}, timeout=0.2)
    assert done == set()
    for task in pending:
        task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await read
    await stream.aclose()
    assert pubsub.closed is True


def test_the_heartbeat_interval_is_shorter_than_a_typical_proxy_timeout() -> None:
    # A proxy that closes an idle connection before the next heartbeat turns the
    # stream into a reconnect loop.
    assert events.HEARTBEAT_SECONDS <= 30
