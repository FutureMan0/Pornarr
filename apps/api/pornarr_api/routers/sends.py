""" "Sent to you" — one person hands a title to another with a note.

The recipient sees who sent it, which is the whole point of the feature, so the
sender's chosen display name travels with the send. The sender does not learn
whether it was watched: the inbox reports `seen_at` to its owner only, and a
send carries no read receipt back.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from pornarr_api.auth import database_session, get_current_user
from pornarr_api.authorship import author_name
from pornarr_api.errors import ErrorResponse
from pornarr_api.routers.ratings import media_or_404
from pornarr_db.models.media import Media
from pornarr_db.models.social import MediaSend
from pornarr_db.models.user import User
from pornarr_shared.errors import PornarrError

router = APIRouter(prefix="/sends", tags=["sends"])
CurrentUser = Annotated[User, Depends(get_current_user)]
Session = Annotated[AsyncSession, Depends(database_session)]

MAXIMUM_PAGE_SIZE = 100


class SendDuplicateError(PornarrError):
    code = "SEND_ALREADY_EXISTS"
    status = 409


class SendSelfError(PornarrError):
    code = "SEND_TO_SELF"
    status = 422


class SendCreate(BaseModel):
    recipient_id: UUID
    media_id: UUID
    note: Annotated[str | None, Field(max_length=1024)] = None

    @field_validator("note")
    @classmethod
    def strip_note(cls, value: str | None) -> str | None:
        stripped = (value or "").strip()
        return stripped or None


class SendResponse(BaseModel):
    id: UUID
    media_id: UUID
    media_title: str
    # Which of the two names is filled depends on the direction being listed;
    # the inbox cares who sent it, the outbox who received it.
    sender: str | None
    recipient: str | None
    note: str | None
    seen_at: datetime | None
    created_at: datetime


async def _responses(
    session: AsyncSession, sends: list[MediaSend], *, incoming: bool
) -> list[SendResponse]:
    if not sends:
        return []
    people_ids = {send.sender_id if incoming else send.recipient_id for send in sends}
    people = {
        user.id: author_name(user)
        for user in await session.scalars(select(User).where(User.id.in_(people_ids)))
    }
    titles = dict(
        (
            await session.execute(
                select(Media.id, Media.title).where(Media.id.in_({send.media_id for send in sends}))
            )
        )
        .tuples()
        .all()
    )
    return [
        SendResponse(
            id=send.id,
            media_id=send.media_id,
            media_title=titles.get(send.media_id, ""),
            sender=people.get(send.sender_id) if incoming else None,
            recipient=None if incoming else people.get(send.recipient_id),
            note=send.note,
            seen_at=send.seen_at,
            created_at=send.created_at,
        )
        for send in sends
    ]


@router.post(
    "",
    response_model=SendResponse,
    status_code=201,
    responses={
        404: {"model": ErrorResponse},
        409: {"model": ErrorResponse},
        422: {"model": ErrorResponse},
    },
)
async def send_media(payload: SendCreate, user: CurrentUser, session: Session) -> SendResponse:
    if payload.recipient_id == user.id:
        raise SendSelfError("A title cannot be sent to yourself.")
    recipient = await session.get(User, payload.recipient_id)
    if recipient is None or not recipient.is_active:
        raise HTTPException(status_code=404)
    await media_or_404(session, payload.media_id)
    send = MediaSend(
        sender_id=user.id,
        recipient_id=payload.recipient_id,
        media_id=payload.media_id,
        note=payload.note,
    )
    session.add(send)
    try:
        await session.flush()
    except IntegrityError as error:
        await session.rollback()
        raise SendDuplicateError("You have already sent this title to that person.") from error
    return (await _responses(session, [send], incoming=False))[0]


@router.get("/received", response_model=list[SendResponse])
async def list_received(
    user: CurrentUser,
    session: Session,
    unseen_only: bool = False,
    limit: Annotated[int, Field(ge=1, le=MAXIMUM_PAGE_SIZE)] = 50,
    offset: Annotated[int, Field(ge=0)] = 0,
) -> list[SendResponse]:
    statement = select(MediaSend).where(MediaSend.recipient_id == user.id)
    if unseen_only:
        statement = statement.where(MediaSend.seen_at.is_(None))
    sends = list(
        await session.scalars(
            statement.order_by(MediaSend.created_at.desc()).limit(limit).offset(offset)
        )
    )
    return await _responses(session, sends, incoming=True)


@router.get("/sent", response_model=list[SendResponse])
async def list_sent(
    user: CurrentUser,
    session: Session,
    limit: Annotated[int, Field(ge=1, le=MAXIMUM_PAGE_SIZE)] = 50,
    offset: Annotated[int, Field(ge=0)] = 0,
) -> list[SendResponse]:
    """Your outbox without read receipts: `seen_at` is blanked for the sender."""

    sends = list(
        await session.scalars(
            select(MediaSend)
            .where(MediaSend.sender_id == user.id)
            .order_by(MediaSend.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
    )
    return [
        response.model_copy(update={"seen_at": None})
        for response in await _responses(session, sends, incoming=False)
    ]


@router.post(
    "/{send_id}/seen", response_model=SendResponse, responses={404: {"model": ErrorResponse}}
)
async def mark_seen(send_id: UUID, user: CurrentUser, session: Session) -> SendResponse:
    send = await session.scalar(
        select(MediaSend).where(MediaSend.id == send_id, MediaSend.recipient_id == user.id)
    )
    if send is None:
        raise HTTPException(status_code=404)
    if send.seen_at is None:
        send.seen_at = datetime.now(UTC)
        await session.flush()
    return (await _responses(session, [send], incoming=True))[0]


@router.delete("/{send_id}", status_code=204, responses={404: {"model": ErrorResponse}})
async def delete_send(send_id: UUID, user: CurrentUser, session: Session) -> None:
    """Either party can remove it: the recipient dismisses, the sender withdraws."""

    send = await session.scalar(
        select(MediaSend).where(
            MediaSend.id == send_id,
            (MediaSend.recipient_id == user.id) | (MediaSend.sender_id == user.id),
        )
    )
    if send is None:
        raise HTTPException(status_code=404)
    await session.delete(send)
