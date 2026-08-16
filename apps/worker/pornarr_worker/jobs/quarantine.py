"""Move uncertain imports out of downloads into a reviewable quarantine."""

from __future__ import annotations

import asyncio
import shutil
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from pathlib import Path
from typing import Any
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from pornarr_core.library_naming import sanitize_component
from pornarr_db.metadata_corrections import apply_metadata_correction
from pornarr_db.models.notification import NotificationKind
from pornarr_db.models.quarantine import QuarantineItem
from pornarr_db.notifications import notify_administrators
from pornarr_db.session import session_scope
from pornarr_db.settings import get_runtime_settings
from pornarr_shared.config import get_settings
from pornarr_shared.jobs import job


class QuarantineReasonCode(StrEnum):
    LOW_CONFIDENCE = "low_confidence"
    FILTER_RULE = "filter_rule"
    UNEXPECTED_FILE_TYPE = "unexpected_file_type"
    CONTRADICTORY_DUPLICATE = "contradictory_duplicate"


@dataclass(frozen=True)
class QuarantineReason:
    """A user-actionable reason, with the rule or threshold that caused it."""

    code: QuarantineReasonCode
    detail: str
    evidence: dict[str, object]

    def as_payload(self) -> dict[str, object]:
        if not self.detail.strip():
            raise ValueError("quarantine reason detail must not be empty")
        return {"code": self.code.value, "detail": self.detail, "evidence": self.evidence}

    @classmethod
    def from_payload(cls, payload: dict[str, object]) -> QuarantineReason:
        code = QuarantineReasonCode(str(payload["code"]))
        detail = str(payload["detail"])
        evidence = payload.get("evidence", {})
        if not isinstance(evidence, dict):
            raise ValueError("quarantine reason evidence must be an object")
        return cls(code=code, detail=detail, evidence=evidence)


async def quarantine_file(
    session: AsyncSession,
    source: Path,
    quarantine_root: Path,
    reasons: list[QuarantineReason],
    *,
    extracted_metadata: dict[str, object] | None = None,
    technical_details: dict[str, object] | None = None,
) -> QuarantineItem:
    """Atomically move one source out of downloads and record why it needs review."""
    if not reasons:
        raise ValueError("at least one quarantine reason is required")

    item_id = uuid4()
    destination = quarantine_root / str(item_id) / sanitize_component(source.name)
    reason_payloads = [reason.as_payload() for reason in reasons]
    metadata = await apply_metadata_correction(session, extracted_metadata or {})
    await asyncio.to_thread(_move_to_quarantine, source, destination)
    item = QuarantineItem(
        id=item_id,
        original_path=str(source),
        quarantine_path=str(destination),
        reasons=reason_payloads,
        extracted_metadata=metadata,
        technical_details=technical_details or {},
    )
    session.add(item)
    try:
        await session.flush()
    except Exception:
        await asyncio.to_thread(_move_to_quarantine, destination, source)
        await asyncio.to_thread(_remove_empty_parents, destination.parent, quarantine_root)
        raise
    return item


async def quarantine_job(
    context: dict[str, Any],
    source_path: str,
    reasons: list[dict[str, object]],
    extracted_metadata: dict[str, object] | None = None,
    technical_details: dict[str, object] | None = None,
) -> str:
    """Run a reviewable quarantine transition on the dedicated import queue."""
    parsed_reasons = [QuarantineReason.from_payload(reason) for reason in reasons]
    settings = get_settings()
    async with session_scope() as session:
        item = await quarantine_file(
            session,
            Path(source_path),
            settings.quarantine_path,
            parsed_reasons,
            extracted_metadata=extracted_metadata,
            technical_details=technical_details,
        )
        await notify_administrators(
            session,
            context["redis"],
            kind=NotificationKind.QUARANTINE_REVIEW,
            payload={"quarantine_item_id": str(item.id), "reasons": item.reasons},
        )
        return str(item.id)


async def prune_quarantine(
    session: AsyncSession,
    quarantine_root: Path,
    retention_days: int,
    *,
    now: datetime | None = None,
) -> int:
    """Delete expired quarantine files and records without following paths outside it."""
    cutoff = (now or datetime.now(UTC)) - timedelta(days=retention_days)
    root = quarantine_root.resolve()
    items = list(
        await session.scalars(select(QuarantineItem).where(QuarantineItem.created_at < cutoff))
    )
    deleted = 0
    for item in items:
        path = Path(item.quarantine_path)
        try:
            path.resolve().relative_to(root)
        except ValueError:
            continue
        await asyncio.to_thread(_delete_quarantined_file, path, root)
        await session.delete(item)
        deleted += 1
    return deleted


async def prune_quarantine_job(_: dict[str, Any]) -> int:
    """Apply the administrator-configured quarantine retention daily."""
    defaults = get_settings()
    async with session_scope() as session:
        runtime = await get_runtime_settings(session, defaults)
        return await prune_quarantine(
            session, defaults.quarantine_path, runtime.quarantine_retention_days
        )


def _move_to_quarantine(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.{uuid4().hex}.tmp")
    try:
        shutil.copy2(source, temporary)
        temporary.replace(destination)
        source.unlink()
    except Exception:
        temporary.unlink(missing_ok=True)
        destination.unlink(missing_ok=True)
        with suppress(OSError):
            destination.parent.rmdir()
        raise


def _delete_quarantined_file(path: Path, root: Path) -> None:
    path.unlink(missing_ok=True)
    _remove_empty_parents(path.parent, root)


def _remove_empty_parents(path: Path, root: Path) -> None:
    while path != root:
        try:
            path.rmdir()
        except OSError:
            return
        path = path.parent


QUARANTINE_JOB = job(quarantine_job)
PRUNE_QUARANTINE_JOB = job(prune_quarantine_job)
