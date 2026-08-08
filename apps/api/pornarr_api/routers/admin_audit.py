"""Administrator access to privacy-preserving audit records."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from pornarr_api.auth import database_session, require_role
from pornarr_db.audit import prune_audit_log
from pornarr_db.models.audit import AuditLog
from pornarr_db.models.user import User, UserRole

router = APIRouter(prefix="/admin/audit", tags=["admin"])
Admin = Annotated[User, Depends(require_role(UserRole.ADMIN))]
Session = Annotated[AsyncSession, Depends(database_session)]


class AuditLogResponse(BaseModel):
    id: UUID
    actor_id: UUID | None
    source: str
    action: str
    target: str | None
    context: dict[str, object]
    created_at: datetime


@router.get("", response_model=list[AuditLogResponse])
async def list_audit_log(
    _: Admin,
    request: Request,
    session: Session,
    actor_id: UUID | None = None,
    action: str | None = None,
    since: Annotated[datetime | None, Query()] = None,
    until: Annotated[datetime | None, Query()] = None,
) -> list[AuditLogResponse]:
    await prune_audit_log(session, request.app.state.settings.audit_retention_days)
    query = select(AuditLog).order_by(AuditLog.created_at.desc()).limit(200)
    if actor_id is not None:
        query = query.where(AuditLog.actor_id == actor_id)
    if action is not None:
        query = query.where(AuditLog.action == action)
    if since is not None:
        query = query.where(AuditLog.created_at >= since)
    if until is not None:
        query = query.where(AuditLog.created_at <= until)
    records = await session.scalars(query)
    return [AuditLogResponse.model_validate(record, from_attributes=True) for record in records]
