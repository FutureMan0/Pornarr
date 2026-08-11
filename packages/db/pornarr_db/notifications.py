"""Preference-aware notification delivery."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from pornarr_db.models.notification import Notification, NotificationKind, NotificationPreference
from pornarr_db.models.user import User, UserRole
from pornarr_shared.events import publish_event


async def notify_user(
    session: AsyncSession,
    redis: Any,
    *,
    user_id: UUID,
    kind: NotificationKind,
    payload: dict[str, object],
) -> Notification | None:
    """Persist and live-deliver one notification unless the user disabled its kind."""
    enabled = await session.scalar(
        select(NotificationPreference.enabled).where(
            NotificationPreference.user_id == user_id, NotificationPreference.kind == kind
        )
    )
    if enabled is False:
        return None
    notification = Notification(user_id=user_id, kind=kind, payload=payload)
    session.add(notification)
    await session.flush()
    await publish_event(
        redis,
        "notification",
        {"id": str(notification.id), "kind": kind.value, "payload": payload},
        user_id=str(user_id),
    )
    return notification


async def notify_administrators(
    session: AsyncSession, redis: Any, *, kind: NotificationKind, payload: dict[str, object]
) -> list[Notification]:
    """Deliver an instance-level notice to every administrator who opted in."""
    administrator_ids = list(
        await session.scalars(select(User.id).where(User.role == UserRole.ADMIN))
    )
    notifications = await _notify_many(
        session, redis, administrator_ids, kind=kind, payload=payload
    )
    return [notification for notification in notifications if notification is not None]


async def _notify_many(
    session: AsyncSession,
    redis: Any,
    user_ids: list[UUID],
    *,
    kind: NotificationKind,
    payload: dict[str, object],
) -> list[Notification | None]:
    return [
        await notify_user(session, redis, user_id=user_id, kind=kind, payload=payload)
        for user_id in user_ids
    ]
