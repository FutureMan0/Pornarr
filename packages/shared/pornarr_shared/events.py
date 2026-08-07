"""Redis-backed event publication shared by API and workers."""

from __future__ import annotations

import json
from typing import Any

EVENT_STREAM = "pornarr:events"
GLOBAL_CHANNEL = "pornarr:events:global"


def user_channel(user_id: str) -> str:
    return f"pornarr:events:user:{user_id}"


async def publish_event(
    redis: Any, event_type: str, data: dict[str, Any], *, user_id: str | None = None
) -> str:
    """Persist an event before publishing its id to every interested API node."""

    fields = {"type": event_type, "data": json.dumps(data), "user_id": user_id or ""}
    event_id = await redis.xadd(EVENT_STREAM, fields)
    if isinstance(event_id, bytes):
        event_id = event_id.decode()
    await redis.publish(user_channel(user_id) if user_id else GLOBAL_CHANNEL, event_id)
    return event_id
