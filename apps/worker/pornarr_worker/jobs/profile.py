"""Nightly incremental interest-profile refresh."""

from __future__ import annotations

from typing import Any

from pornarr_db.preferences import refresh_interest_profiles
from pornarr_db.session import session_scope
from pornarr_shared.jobs import job


async def refresh_interest_profiles_job(_: dict[str, Any]) -> int:
    """Refresh every profile from events that arrived since the previous run."""

    async with session_scope() as session:
        return await refresh_interest_profiles(session)


REFRESH_INTEREST_PROFILES_JOB = job(refresh_interest_profiles_job)
