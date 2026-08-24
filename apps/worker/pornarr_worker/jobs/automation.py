"""Fail-closed automatic request execution."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from pornarr_db.automation import AutomationCandidate, execute_automation
from pornarr_db.session import session_scope
from pornarr_db.settings import get_runtime_settings
from pornarr_shared.config import get_settings
from pornarr_shared.jobs import job


async def automation_execute(
    _: dict[str, Any],
    *,
    user_id: str,
    release_guid: str,
    query: str,
    score: float,
    size_bytes: int,
    quality: str,
    metadata_confident: bool,
    tags: list[str],
    performers: list[str],
    breakdown: dict[str, float],
) -> str:
    """Evaluate one candidate and return its durable decision reason."""

    candidate = AutomationCandidate(
        user_id=UUID(user_id),
        release_guid=release_guid,
        query=query,
        score=score,
        size_bytes=size_bytes,
        quality=quality,
        metadata_confident=metadata_confident,
        tags=tuple(tags),
        performers=tuple(performers),
        breakdown=breakdown,
    )
    defaults = get_settings()
    async with session_scope() as session:
        settings = await get_runtime_settings(session, defaults)
        decision = await execute_automation(session, candidate, settings)
    return "requested" if decision.created else decision.reason or "refused"


AUTOMATION_EXECUTION_JOB = job(automation_execute)
