"""Scheduled user-event retention."""

from __future__ import annotations

from typing import Any

from pornarr_db.events import prune_user_events
from pornarr_db.session import session_scope
from pornarr_db.settings import get_runtime_settings
from pornarr_shared.config import get_settings
from pornarr_shared.jobs import job


async def prune_user_events_job(_: dict[str, Any]) -> None:
    """Apply the current event-retention setting once per day."""

    defaults = get_settings()
    async with session_scope() as session:
        settings = await get_runtime_settings(session, defaults)
        await prune_user_events(session, settings.user_event_retention_days)


PRUNE_USER_EVENTS_JOB = job(prune_user_events_job)
