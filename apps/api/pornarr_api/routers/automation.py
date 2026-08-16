"""Per-user automation controls, usage, and private decision history."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from pornarr_api.auth import database_session, get_current_user
from pornarr_db.automation import AutomationRuleWrite, save_automation_rule
from pornarr_db.models.audit import AuditLog
from pornarr_db.models.automation import AutomationRule
from pornarr_db.models.user import User
from pornarr_db.settings import get_runtime_settings
from pornarr_db.storage import daily_storage_usage

router = APIRouter(prefix="/automation", tags=["automation"])
CurrentUser = Annotated[User, Depends(get_current_user)]
Session = Annotated[AsyncSession, Depends(database_session)]


class AutomationUsageResponse(BaseModel):
    downloaded_bytes: int
    reserved_bytes: int
    download_count: int
    reserved_download_count: int


class AutomationDecisionResponse(BaseModel):
    id: UUID
    action: str
    target: str | None
    context: dict[str, object]
    created_at: datetime


class AutomationResponse(AutomationRuleWrite):
    usage: AutomationUsageResponse
    decisions: list[AutomationDecisionResponse]


async def response_for(user: User, request: Request, session: AsyncSession) -> AutomationResponse:
    settings = await get_runtime_settings(session, request.app.state.settings)
    rule = await session.get(AutomationRule, user.id)
    if rule is None:
        rule = await save_automation_rule(session, user.id, AutomationRuleWrite(), settings)
    usage = await daily_storage_usage(session, user.id)
    records = await session.scalars(
        select(AuditLog)
        .where(AuditLog.actor_id == user.id, AuditLog.action.in_(["automation.requested", "automation.refused"]))
        .order_by(AuditLog.created_at.desc())
        .limit(100)
    )
    return AutomationResponse(
        **AutomationRuleWrite.model_validate(rule, from_attributes=True).model_dump(),
        usage=AutomationUsageResponse(
            downloaded_bytes=usage.downloaded_bytes,
            reserved_bytes=usage.reserved_bytes,
            download_count=usage.download_count,
            reserved_download_count=usage.reserved_download_count,
        ),
        decisions=[AutomationDecisionResponse.model_validate(record, from_attributes=True) for record in records],
    )


@router.get("", response_model=AutomationResponse)
async def read_automation(user: CurrentUser, request: Request, session: Session) -> AutomationResponse:
    return await response_for(user, request, session)


@router.put("", response_model=AutomationResponse)
async def write_automation(
    payload: AutomationRuleWrite, user: CurrentUser, request: Request, session: Session
) -> AutomationResponse:
    settings = await get_runtime_settings(session, request.app.state.settings)
    await save_automation_rule(session, user.id, payload, settings)
    return await response_for(user, request, session)


@router.post("/kill", response_model=AutomationResponse)
async def kill_automation(user: CurrentUser, request: Request, session: Session) -> AutomationResponse:
    settings = await get_runtime_settings(session, request.app.state.settings)
    existing = await session.get(AutomationRule, user.id)
    values = AutomationRuleWrite() if existing is None else AutomationRuleWrite.model_validate(existing, from_attributes=True)
    await save_automation_rule(session, user.id, values.model_copy(update={"enabled": False}), settings)
    return await response_for(user, request, session)
