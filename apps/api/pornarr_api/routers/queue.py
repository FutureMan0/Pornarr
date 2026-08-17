"""Administrator queue visibility and recalculated wait estimates."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func, literal, select, tuple_
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
    # What somebody asked for, resolved here. A queue that lists release GUIDs
    # is a queue nobody can read; absent when the job has no request behind it,
    # in which case the GUID is all there is and the client says so.
    title: str | None
    client_name: str
    protocol: str
    release_guid: str
    status: str
    priority: int
    remaining_bytes: int | None
    size_bytes: int | None
    download_speed_bytes: int | None
    error: str | None
    estimated_seconds: int | None
    created_at: datetime
    queue_estimate: QueueEstimateResponse


class QueueListResponse(BaseModel):
    items: list[QueueJobResponse]
    next_cursor: str | None


def _item(
    job: DownloadJob,
    requests: dict[UUID, tuple[UUID, str]],
    waiting: list[Job],
) -> QueueJobResponse:
    """One row, with its request's wording where there is a request."""
    found = requests.get(job.id)
    request_id, title = found if found is not None else (None, None)
    return queue_response(job, request_id, waiting, title=title)


def queue_response(
    job: DownloadJob,
    request_id: UUID | None,
    waiting: list[Job],
    title: str | None = None,
) -> QueueJobResponse:
    estimate = (
        queued_estimate(priority=job.priority, waiting=waiting)
        if job.status in _WAITING_STATUSES
        else unknown()
    )
    return QueueJobResponse(
        id=job.id,
        request_id=request_id,
        title=title,
        client_name=job.client_name,
        protocol=job.protocol,
        release_guid=job.release_guid,
        status=job.status,
        priority=job.priority,
        remaining_bytes=job.remaining_bytes,
        size_bytes=job.size_bytes,
        download_speed_bytes=job.download_speed_bytes,
        error=job.error,
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
    requests = (
        {
            download_job_id: (request_id, query)
            for download_job_id, request_id, query in (
                await session.execute(
                    select(Request.download_job_id, Request.id, Request.query).where(
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
        items=[_item(job, requests, waiting) for job in page],
        next_cursor=next_cursor,
    )


class QueueSummaryResponse(BaseModel):
    """The four cards above the queue.

    Counted over the whole queue rather than over the page. A card that
    describes what happened to load is a card that changes when you scroll,
    which is the one thing a summary must not do.
    """

    active: int
    queued: int
    failed: int
    completed: int
    # Summed over the jobs actually moving bytes. A client that reports no speed
    # contributes nothing rather than a zero that drags the total down.
    speed_bytes: int


# Moving bytes right now, as opposed to waiting for a turn.
_ACTIVE_STATUSES = frozenset({"checking", "downloading", "importing", "moving", "repairing"})


@router.get("/summary", response_model=QueueSummaryResponse)
async def queue_summary(_: Admin, session: Session) -> QueueSummaryResponse:
    counts = {
        str(status): int(count)
        for status, count in (
            await session.execute(
                select(DownloadJob.status, func.count()).group_by(DownloadJob.status)
            )
        ).tuples()
    }
    speed = await session.scalar(
        select(func.sum(DownloadJob.download_speed_bytes)).where(
            DownloadJob.status.in_(_ACTIVE_STATUSES)
        )
    )
    return QueueSummaryResponse(
        active=sum(count for status, count in counts.items() if status in _ACTIVE_STATUSES),
        queued=counts.get("queued", 0),
        failed=counts.get("failed", 0),
        completed=counts.get("completed", 0),
        speed_bytes=int(speed or 0),
    )
