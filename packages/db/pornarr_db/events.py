"""Transactional, privacy-preserving recommendation-event storage."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from pornarr_db.models.playback import UserEvent, UserEventType


async def record_user_event(
    session: AsyncSession,
    user_id: UUID,
    event_type: UserEventType,
    *,
    media_id: UUID | None = None,
    value: float | None = None,
    subject_id: UUID | None = None,
) -> UserEvent:
    """Stage one opaque user signal in the caller's transaction."""

    event = UserEvent(
        user_id=user_id,
        event_type=event_type.value,
        media_id=media_id,
        value=value,
        subject_id=subject_id,
    )
    session.add(event)
    return event


async def prune_user_events(session: AsyncSession, retention_days: int | None) -> None:
    """Remove events outside the operator-selected retention window."""

    if retention_days is not None:
        await session.execute(
            delete(UserEvent)
            .where(UserEvent.created_at < datetime.now(UTC) - timedelta(days=retention_days))
            .execution_options(synchronize_session=False)
        )
