"""Authenticated in-app notification inbox and preferences."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Annotated, Any, cast
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from pornarr_api.auth import database_session, get_current_user
from pornarr_api.errors import ErrorResponse
from pornarr_db.models.notification import Notification, NotificationKind, NotificationPreference
from pornarr_db.models.user import User

router = APIRouter(prefix="/notifications", tags=["notifications"])
CurrentUser = Annotated[User, Depends(get_current_user)]
Session = Annotated[AsyncSession, Depends(database_session)]
_AUTHENTICATION_ERRORS: dict[int | str, dict[str, Any]] = {401: {"model": ErrorResponse}}


class NotificationResponse(BaseModel):
    id: UUID
    kind: NotificationKind
    payload: dict[str, object]
    read_at: datetime | None
    created_at: datetime


class NotificationPreferenceWrite(BaseModel):
    enabled: bool


class NotificationPreferenceResponse(BaseModel):
    kind: NotificationKind
    enabled: bool


def notification_response(notification: Notification) -> NotificationResponse:
    return NotificationResponse.model_validate(notification, from_attributes=True)


@router.get("", response_model=list[NotificationResponse], responses=_AUTHENTICATION_ERRORS)
async def list_notifications(
    user: CurrentUser, session: Session, unread: bool = False
) -> list[NotificationResponse]:
    statement = select(Notification).where(Notification.user_id == user.id)
    if unread:
        statement = statement.where(Notification.read_at.is_(None))
    notifications = list(await session.scalars(statement.order_by(Notification.created_at.desc())))
    return [notification_response(notification) for notification in notifications]


@router.post(
    "/{notification_id}/read",
    response_model=NotificationResponse,
    responses={**_AUTHENTICATION_ERRORS, 404: {"model": ErrorResponse}},
)
async def mark_read(
    notification_id: UUID, user: CurrentUser, session: Session
) -> NotificationResponse:
    notification = await session.scalar(
        select(Notification).where(
            Notification.id == notification_id, Notification.user_id == user.id
        )
    )
    if notification is None:
        raise HTTPException(status_code=404)
    if notification.read_at is None:
        notification.read_at = datetime.now(UTC)
        await session.flush()
    return notification_response(notification)


@router.post("/read-all", status_code=204, responses=_AUTHENTICATION_ERRORS)
async def mark_all_read(user: CurrentUser, session: Session) -> None:
    await session.execute(
        update(Notification)
        .where(Notification.user_id == user.id, Notification.read_at.is_(None))
        .values(read_at=datetime.now(UTC))
    )


@router.get(
    "/preferences",
    response_model=list[NotificationPreferenceResponse],
    responses=_AUTHENTICATION_ERRORS,
)
async def list_preferences(
    user: CurrentUser, session: Session
) -> list[NotificationPreferenceResponse]:
    preferences = await session.execute(
        select(NotificationPreference.kind, NotificationPreference.enabled).where(
            NotificationPreference.user_id == user.id
        )
    )
    enabled_by_kind = dict(cast(Iterable[tuple[NotificationKind, bool]], preferences.tuples()))
    return [
        NotificationPreferenceResponse(kind=kind, enabled=enabled_by_kind.get(kind, True))
        for kind in NotificationKind
    ]


@router.put(
    "/preferences/{kind}",
    response_model=NotificationPreferenceResponse,
    responses={**_AUTHENTICATION_ERRORS, 422: {"model": ErrorResponse}},
)
async def set_preference(
    kind: NotificationKind,
    payload: NotificationPreferenceWrite,
    user: CurrentUser,
    session: Session,
) -> NotificationPreferenceResponse:
    preference = await session.scalar(
        select(NotificationPreference).where(
            NotificationPreference.user_id == user.id, NotificationPreference.kind == kind
        )
    )
    if preference is None:
        preference = NotificationPreference(user_id=user.id, kind=kind, enabled=payload.enabled)
        session.add(preference)
    else:
        preference.enabled = payload.enabled
    await session.flush()
    return NotificationPreferenceResponse(kind=preference.kind, enabled=preference.enabled)
