"""The watch-later queue behind the tab bar.

Private and unshareable by design. Unlike a collection it has no name, no
visibility and no owner other than the person reading it, so there is nothing
here to expose to anyone else.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from pornarr_api.auth import database_session, get_current_user
from pornarr_api.errors import ErrorResponse
from pornarr_api.idempotency import insert_once
from pornarr_api.routers.ratings import media_or_404
from pornarr_db.models.media import Media
from pornarr_db.models.user import User
from pornarr_db.models.watchlist import WatchlistEntry

router = APIRouter(prefix="/watchlist", tags=["watchlist"])
CurrentUser = Annotated[User, Depends(get_current_user)]
Session = Annotated[AsyncSession, Depends(database_session)]


class WatchlistWrite(BaseModel):
    media_id: UUID


class WatchlistItemResponse(BaseModel):
    media_id: UUID
    title: str
    added_at: datetime


@router.get("", response_model=list[WatchlistItemResponse])
async def list_watchlist(user: CurrentUser, session: Session) -> list[WatchlistItemResponse]:
    rows = (
        await session.execute(
            select(WatchlistEntry.media_id, Media.title, WatchlistEntry.created_at)
            .join(Media, Media.id == WatchlistEntry.media_id)
            .where(WatchlistEntry.user_id == user.id)
            .order_by(WatchlistEntry.created_at.desc())
        )
    ).tuples()
    return [
        WatchlistItemResponse(media_id=media_id, title=title, added_at=added_at)
        for media_id, title, added_at in rows
    ]


@router.post(
    "",
    status_code=204,
    response_class=Response,
    responses={404: {"model": ErrorResponse}},
)
async def add_to_watchlist(
    payload: WatchlistWrite, user: CurrentUser, session: Session
) -> Response:
    """Idempotent: adding something already queued is not an error."""

    await media_or_404(session, payload.media_id)
    await insert_once(session, WatchlistEntry(user_id=user.id, media_id=payload.media_id))
    return Response(status_code=204)


@router.delete("/{media_id}", status_code=204, responses={404: {"model": ErrorResponse}})
async def remove_from_watchlist(media_id: UUID, user: CurrentUser, session: Session) -> None:
    entry = await session.scalar(
        select(WatchlistEntry).where(
            WatchlistEntry.user_id == user.id, WatchlistEntry.media_id == media_id
        )
    )
    if entry is None:
        raise HTTPException(status_code=404)
    await session.delete(entry)
