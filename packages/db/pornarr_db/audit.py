"""Transactional audit writer shared by API and workers."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from pornarr_db.models.audit import AuditLog
from pornarr_shared.audit import AuditSource


def write_audit(
    session: AsyncSession,
    *,
    actor_id: UUID | None,
    source: AuditSource = AuditSource.SESSION,
    action: str,
    target: str | None = None,
    context: dict[str, Any] | None = None,
) -> AuditLog:
    """Stage one privacy-safe audit record in the caller's transaction."""

    record = AuditLog(
        actor_id=actor_id,
        source=source.value,
        action=action,
        target=target,
        context=context or {},
    )
    session.add(record)
    return record


async def prune_audit_log(session: AsyncSession, retention_days: int | None) -> None:
    """Delete expired records when the operator configured a retention period."""

    if retention_days is not None:
        await session.execute(
            delete(AuditLog).where(
                AuditLog.created_at < datetime.now(UTC) - timedelta(days=retention_days)
            )
        )
