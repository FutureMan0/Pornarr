"""Administrator queue visibility and recalculated wait estimates."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import literal, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from pornarr_api.auth import database_session, require_role
from pornarr_core.eta import Confidence, Job, queued_estimate, unknown
from pornarr_db.media_search import decode_cursor, encode_cursor
from pornarr_db.models.download import DownloadJob
from pornarr_db.models.request import Request
from pornarr_db.models.user import User, UserRole

router = APIRouter(prefix="/queue", tags=["queue"])
Admin = Annotated[User, Depends(require_role(UserRole.ADMIN))]
Session = Annotated[AsyncSession, Depends(database_session)]

_TERMINAL_STATUSES = frozenset({"completed", "failed", "removed"})
_WAITING_STATUSES = frozenset(
    {"checking", "downloading", "importing", "metadata", "moving", "queued", "repairing", "stalled"}
)


class QueueSort(StrEnum):
    PRIORITY = "priority"
    CREATED_AT = "created_at"
    STATUS = "status"


class QueueEstimateResponse(BaseModel):
    low_seconds: int | None
    high_seconds: int | None
    confidence: Confidence


class QueueJobResponse(BaseModel):
    id: UUID
    request_id: UUID | None
    client_name: str
    protocol: str
    release_guid: str
    status: str
    priority: int
    remaining_bytes: int | None
    estimated_seconds: int | None
    created_at: datetime
    queue_estimate: QueueEstimateResponse


class QueueListResponse(BaseModel):
    items: list[QueueJobResponse]
    next_cursor: str | None


def queue_response(
    job: DownloadJob, request_id: UUID | None, waiting: list[Job]
) -> QueueJobResponse:
    estimate = (
        queued_estimate(priority=job.priority, waiting=waiting)
        if job.status in _WAITING_STATUSES
        else unknown()
    )
    return QueueJobResponse(
        id=job.id,
        request_id=request_id,
        client_name=job.client_name,
        protocol=job.protocol,
        release_guid=job.release_guid,
        status=job.status,
        priority=job.priority,
        remaining_bytes=job.remaining_bytes,
        estimated_seconds=job.estimated_seconds,
        created_at=job.created_at,
        queue_estimate=QueueEstimateResponse(
            low_seconds=estimate.low_seconds,
            high_seconds=estimate.high_seconds,
            confidence=estimate.confidence,
        ),
    )


@router.get("", response_model=QueueListResponse)
async def list_queue(
    _: Admin,
    session: Session,
    status: str | None = None,
    download_client_id: UUID | None = None,
    sort: QueueSort = QueueSort.PRIORITY,
    cursor: str | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> QueueListResponse:
    """List queue jobs with deterministic sorting and current priority-based ETAs."""

    filters = []
    if status is not None:
        filters.append(DownloadJob.status == status)
    if download_client_id is not None:
        filters.append(DownloadJob.download_client_id == download_client_id)
    sort_column = {
        QueueSort.PRIORITY: DownloadJob.priority,
        QueueSort.CREATED_AT: DownloadJob.created_at,
        QueueSort.STATUS: DownloadJob.status,
    }[sort]
    if cursor is not None:
        try:
            value, job_id = decode_cursor(cursor)
        except ValueError as error:
            raise HTTPException(status_code=422, detail="Invalid queue cursor.") from error
        filters.append(
            tuple_(sort_column, DownloadJob.id) < tuple_(literal(value), literal(job_id))
        )
    jobs = list(
        await session.scalars(
            select(DownloadJob)
            .where(*filters)
            .order_by(sort_column.desc(), DownloadJob.id.desc())
            .limit(limit + 1)
        )
    )
    page = jobs[:limit]
    active_jobs = list(
        await session.scalars(
            select(DownloadJob).where(
                DownloadJob.status.not_in(_TERMINAL_STATUSES | {"paused"}),
                DownloadJob.estimated_seconds.is_not(None),
            )
        )
    )
    waiting = [
        Job(priority=job.priority, remaining_seconds=job.estimated_seconds)
        for job in active_jobs
        if job.estimated_seconds is not None
    ]
    request_ids = (
        {
            download_job_id: request_id
            for download_job_id, request_id in (
                await session.execute(
                    select(Request.download_job_id, Request.id).where(
                        Request.download_job_id.in_([job.id for job in page])
                    )
                )
            ).tuples()
            if download_job_id is not None
        }
        if page
        else {}
    )
    next_cursor = None
    if len(jobs) > limit:
        last = page[-1]
        next_cursor = encode_cursor((getattr(last, sort.value), last.id))
    return QueueListResponse(
        items=[queue_response(job, request_ids.get(job.id), waiting) for job in page],
        next_cursor=next_cursor,
    )
