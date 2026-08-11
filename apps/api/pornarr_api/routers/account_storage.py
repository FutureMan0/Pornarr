"""Authenticated access to the caller's daily storage budget."""

from __future__ import annotations

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from pornarr_api.auth import database_session
from pornarr_api.routers.account import CurrentUser
from pornarr_db.storage import daily_storage_usage

router = APIRouter(prefix="/account/storage", tags=["account"])
Session = Annotated[AsyncSession, Depends(database_session)]


class DailyStorageUsageResponse(BaseModel):
    day: date
    downloaded_bytes: int
    download_count: int


@router.get("", response_model=DailyStorageUsageResponse)
async def read_daily_storage_usage(
    user: CurrentUser, session: Session
) -> DailyStorageUsageResponse:
    usage = await daily_storage_usage(session, user.id)
    return DailyStorageUsageResponse(
        day=usage.day,
        downloaded_bytes=usage.downloaded_bytes,
        download_count=usage.download_count,
    )
