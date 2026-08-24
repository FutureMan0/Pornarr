"""Authenticated, resumable server-sent events."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from contextlib import suppress
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse

from pornarr_api.auth import get_streaming_user
from pornarr_db.models.user import User
from pornarr_shared.events import EVENT_STREAM, GLOBAL_CHANNEL, user_channel

router = APIRouter(tags=["events"])
HEARTBEAT_SECONDS = 15


def _event_frame(event_id: str, fields: dict[str, Any]) -> str | None:
    audience = fields["user_id"]
    if isinstance(audience, bytes):
        audience = audience.decode()
    if audience not in ("", fields.get("viewer_id")):
        return None
    event_type = fields["type"]
    data = fields["data"]
    if isinstance(event_type, bytes):
        event_type = event_type.decode()
    if isinstance(data, bytes):
        data = data.decode()
    return f"id: {event_id}\nevent: {event_type}\ndata: {data}\n\n"


async def _next_message(pubsub: Any, shutting_down: asyncio.Event | None) -> dict[str, Any] | None:
    """The next published message, or None on heartbeat or shutdown.

    Racing the two rather than polling: `get_message` blocks for the whole
    heartbeat interval, so checking a flag only between calls would still leave
    shutdown waiting up to fifteen seconds per open stream.
    """
    read = asyncio.ensure_future(
        pubsub.get_message(ignore_subscribe_messages=True, timeout=HEARTBEAT_SECONDS)
    )
    if shutting_down is None:
        return await read

    stop = asyncio.ensure_future(shutting_down.wait())
    try:
        done, _ = await asyncio.wait({read, stop}, return_when=asyncio.FIRST_COMPLETED)
        if read in done:
            return read.result()
        return None
    finally:
        for task in (read, stop):
            if not task.done():
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task


async def _stream(request: Request, user_id: str, last_event_id: str | None) -> AsyncGenerator[str]:
    redis = request.app.state.redis
    if last_event_id:
        replay = await redis.xread({EVENT_STREAM: last_event_id})
        for _, events in replay:
            for event_id, fields in events:
                fields["viewer_id"] = user_id
                frame = _event_frame(event_id, fields)
                if frame:
                    yield frame
    # Set when the process starts shutting down. A stream that only watches the
    # client keeps uvicorn's graceful shutdown waiting for as long as a tab is
    # open, which turns every SIGTERM into a forced kill.
    shutting_down: asyncio.Event | None = getattr(request.app.state, "shutting_down", None)

    pubsub = redis.pubsub()
    await pubsub.subscribe(GLOBAL_CHANNEL, user_channel(user_id))
    try:
        while not await request.is_disconnected():
            if shutting_down is not None and shutting_down.is_set():
                break
            message = await _next_message(pubsub, shutting_down)
            if message is None:
                # Either the heartbeat interval passed with nothing to send, or
                # shutdown interrupted the wait. The next loop condition tells
                # the two apart; a heartbeat on the way out is harmless.
                if shutting_down is not None and shutting_down.is_set():
                    break
                yield ": heartbeat\n\n"
                continue
            event_id = message["data"]
            if isinstance(event_id, bytes):
                event_id = event_id.decode()
            records = await redis.xrange(EVENT_STREAM, min=event_id, max=event_id)
            for _, fields in records:
                fields["viewer_id"] = user_id
                frame = _event_frame(event_id, fields)
                if frame:
                    yield frame
    except asyncio.CancelledError:
        raise
    finally:
        await pubsub.unsubscribe(GLOBAL_CHANNEL, user_channel(user_id))
        await pubsub.aclose()


@router.get("/events", response_class=StreamingResponse)
async def events(
    request: Request,
    user: Annotated[User, Depends(get_streaming_user)],
) -> StreamingResponse:
    return StreamingResponse(
        _stream(request, str(user.id), request.headers.get("Last-Event-ID")),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )
