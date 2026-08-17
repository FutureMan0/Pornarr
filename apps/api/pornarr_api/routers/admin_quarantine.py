"""Administrator review actions for files withheld from the library."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
from datetime import date, datetime
from pathlib import Path
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from pornarr_api.auth import database_session, require_role
from pornarr_core.library_placement import PlacementMethod, place_file
from pornarr_db.audit import write_audit
from pornarr_db.metadata_corrections import store_metadata_correction
from pornarr_db.models.download import ImportTrigger
from pornarr_db.models.media import Media, MediaFile
from pornarr_db.models.quarantine import QuarantineItem
from pornarr_db.models.request import Request as RequestRecord
from pornarr_db.models.user import User, UserRole
from pornarr_shared.errors import PornarrError

router = APIRouter(prefix="/admin/quarantine", tags=["admin"])
Admin = Annotated[User, Depends(require_role(UserRole.ADMIN))]
Session = Annotated[AsyncSession, Depends(database_session)]


class QuarantineReviewError(PornarrError):
    code = "QUARANTINE_REVIEW_INVALID"
    status = 422


class QuarantineItemResponse(BaseModel):
    id: UUID
    original_path: str
    quarantine_path: str
    reasons: list[dict[str, object]]
    extracted_metadata: dict[str, object]
    technical_details: dict[str, object]
    created_at: datetime


class QuarantineApproval(BaseModel):
    # Whose library the approved title joins. Null keeps it in the shared pool,
    # which is where everything lands on a server that never turned private
    # libraries on.
    target_owner_id: UUID | None = None
    title: Annotated[str | None, Field(min_length=1, max_length=512)] = None
    studio: Annotated[str | None, Field(max_length=256)] = None
    release_date: date | None = None
    quality: Annotated[str | None, Field(max_length=64)] = None


class ApprovalResponse(BaseModel):
    media_id: UUID
    path: str
    placement_method: PlacementMethod


class BulkPreviewRequest(BaseModel):
    item_ids: Annotated[list[UUID], Field(min_length=1, max_length=100)]
    reason_code: Annotated[str, Field(min_length=1, max_length=64)]


class BulkPreviewResponse(BaseModel):
    items: list[QuarantineItemResponse]
    reason_code: str
    token: str


class BulkApprovalRequest(BulkPreviewRequest):
    token: Annotated[str, Field(min_length=64, max_length=64)]


class BulkApprovalResponse(BaseModel):
    approved: list[ApprovalResponse]


@router.get("", response_model=list[QuarantineItemResponse])
async def list_quarantine_items(_: Admin, session: Session) -> list[QuarantineItemResponse]:
    items = await session.scalars(select(QuarantineItem).order_by(QuarantineItem.created_at))
    return [_item_response(item) for item in items]


@router.post("/items/{item_id}/approve", response_model=ApprovalResponse)
async def approve_quarantine_item(
    item_id: UUID,
    payload: QuarantineApproval,
    request: Request,
    user: Admin,
    session: Session,
) -> ApprovalResponse:
    item = await _item_or_404(session, item_id)
    response = await _approve_item(session, request, user, item, payload)
    write_audit(
        session,
        actor_id=user.id,
        action="quarantine.approved",
        target=str(item_id),
        context={"media_id": str(response.media_id), "corrected": bool(payload.model_fields_set)},
    )
    return response


@router.post("/items/{item_id}/reject", status_code=204)
async def reject_quarantine_item(
    item_id: UUID, request: Request, user: Admin, session: Session
) -> None:
    item = await _item_or_404(session, item_id)
    path = _quarantine_path(item, request)
    await asyncio.to_thread(path.unlink, missing_ok=True)
    await session.delete(item)
    write_audit(
        session,
        actor_id=user.id,
        action="quarantine.rejected",
        target=str(item_id),
        context={"file_deleted": True},
    )


@router.post("/bulk/preview", response_model=BulkPreviewResponse)
async def preview_bulk_approval(
    payload: BulkPreviewRequest, request: Request, _: Admin, session: Session
) -> BulkPreviewResponse:
    items = await _items_for_bulk(session, payload.item_ids, payload.reason_code)
    return BulkPreviewResponse(
        items=[_item_response(item) for item in items],
        reason_code=payload.reason_code,
        token=_bulk_token(request, items, payload.reason_code),
    )


@router.post("/bulk/approve", response_model=BulkApprovalResponse)
async def approve_bulk_quarantine_items(
    payload: BulkApprovalRequest, request: Request, user: Admin, session: Session
) -> BulkApprovalResponse:
    items = await _items_for_bulk(session, payload.item_ids, payload.reason_code)
    if not hmac.compare_digest(payload.token, _bulk_token(request, items, payload.reason_code)):
        raise QuarantineReviewError("The bulk approval must be previewed before it is confirmed.")
    approved = [
        await _approve_item(session, request, user, item, QuarantineApproval()) for item in items
    ]
    write_audit(
        session,
        actor_id=user.id,
        action="quarantine.bulk_approved",
        context={"item_ids": [str(item.id) for item in items], "reason_code": payload.reason_code},
    )
    return BulkApprovalResponse(approved=approved)


async def _approve_item(
    session: AsyncSession,
    request: Request,
    user: User,
    item: QuarantineItem,
    payload: QuarantineApproval,
) -> ApprovalResponse:
    source = _quarantine_path(item, request)
    metadata = item.extracted_metadata
    title = payload.title or _metadata_string(metadata, "title") or source.stem
    studio = (
        payload.studio
        if "studio" in payload.model_fields_set
        else _metadata_string(metadata, "studio")
    )
    release_date = (
        payload.release_date
        if "release_date" in payload.model_fields_set
        else _metadata_date(metadata, "release_date")
    )
    quality = (
        payload.quality
        if "quality" in payload.model_fields_set
        else _metadata_string(metadata, "quality")
    )
    placed = await asyncio.to_thread(
        place_file,
        source,
        request.app.state.settings.library_path,
        studio,
        title,
        release_date.isoformat() if release_date is not None else None,
        quality=quality,
    )
    try:
        file_stat = await asyncio.to_thread(placed.path.stat)
        media = Media(
            title=title,
            normalized_title=title.casefold(),
            owner_id=(
                payload.target_owner_id
                if "target_owner_id" in payload.model_fields_set
                else await _requested_target_owner(session, item)
            ),
            studio=studio,
            release_date=release_date,
            confidence=_metadata_float(metadata, "confidence"),
        )
        session.add(
            MediaFile(
                media=media,
                path=str(placed.path),
                size=file_stat.st_size,
                modified_at_ns=file_stat.st_mtime_ns,
                quality=quality,
                container=_metadata_string(item.technical_details, "container"),
                resolution=_metadata_string(item.technical_details, "resolution"),
                duration_seconds=_metadata_float(item.technical_details, "duration_seconds"),
                bitrate=_metadata_int(item.technical_details, "bitrate"),
            )
        )
        if payload.model_fields_set and (source_title := _metadata_string(metadata, "title")):
            await store_metadata_correction(
                session,
                source_title=source_title,
                title=title,
                studio=studio,
                release_date=release_date,
                quality=quality,
                created_by_id=user.id,
            )
        await session.flush()
        await asyncio.to_thread(source.unlink)
        await session.delete(item)
        return ApprovalResponse(
            media_id=media.id, path=str(placed.path), placement_method=placed.method
        )
    except Exception:
        await asyncio.to_thread(placed.path.unlink, missing_ok=True)
        raise


async def _requested_target_owner(session: AsyncSession, item: QuarantineItem) -> UUID | None:
    """Whose library the request behind this file aimed at, if it came from one.

    The path is the only identity a quarantined file keeps: the trigger records
    the completed download it was staged from, and the request records the job
    it grabbed. Without this the target chosen at request time would be lost the
    moment an import needed review.
    """
    return await session.scalar(
        select(RequestRecord.target_owner_id)
        .join(ImportTrigger, ImportTrigger.download_job_id == RequestRecord.download_job_id)
        .where(ImportTrigger.source_path == item.original_path)
    )


async def _item_or_404(session: AsyncSession, item_id: UUID) -> QuarantineItem:
    item = await session.get(QuarantineItem, item_id)
    if item is None:
        raise HTTPException(status_code=404)
    return item


async def _items_for_bulk(
    session: AsyncSession, item_ids: list[UUID], reason_code: str
) -> list[QuarantineItem]:
    unique_ids = list(dict.fromkeys(item_ids))
    if len(unique_ids) != len(item_ids):
        raise QuarantineReviewError("A bulk review cannot include the same item twice.")
    items = list(
        await session.scalars(select(QuarantineItem).where(QuarantineItem.id.in_(unique_ids)))
    )
    by_id = {item.id: item for item in items}
    if len(by_id) != len(unique_ids):
        raise HTTPException(status_code=404)
    ordered = [by_id[item_id] for item_id in unique_ids]
    if any(not _has_reason(item, reason_code) for item in ordered):
        raise QuarantineReviewError("Every bulk item must share the selected reason.")
    return ordered


def _quarantine_path(item: QuarantineItem, request: Request) -> Path:
    root = request.app.state.settings.quarantine_path.resolve()
    path = Path(item.quarantine_path).resolve()
    try:
        path.relative_to(root)
    except ValueError as error:
        raise QuarantineReviewError(
            "The stored quarantine path is outside the quarantine root."
        ) from error
    return path


def _item_response(item: QuarantineItem) -> QuarantineItemResponse:
    return QuarantineItemResponse.model_validate(item, from_attributes=True)


def _has_reason(item: QuarantineItem, reason_code: str) -> bool:
    return any(reason.get("code") == reason_code for reason in item.reasons)


def _bulk_token(request: Request, items: list[QuarantineItem], reason_code: str) -> str:
    payload = json.dumps(
        {
            "items": [
                {"id": str(item.id), "updated_at": item.updated_at.isoformat()}
                for item in sorted(items, key=lambda candidate: str(candidate.id))
            ],
            "reason_code": reason_code,
        },
        separators=(",", ":"),
    )
    secret = request.app.state.settings.app_secret.get_secret_value().encode()
    return hmac.new(secret, payload.encode(), hashlib.sha256).hexdigest()


def _metadata_string(metadata: dict[str, Any], key: str) -> str | None:
    value = metadata.get(key)
    return value if isinstance(value, str) and value.strip() else None


def _metadata_date(metadata: dict[str, Any], key: str) -> date | None:
    value = _metadata_string(metadata, key)
    if value is None:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _metadata_float(metadata: dict[str, Any], key: str) -> float | None:
    value = metadata.get(key)
    return float(value) if isinstance(value, int | float) else None


def _metadata_int(metadata: dict[str, Any], key: str) -> int | None:
    value = metadata.get(key)
    return value if isinstance(value, int) and not isinstance(value, bool) else None
