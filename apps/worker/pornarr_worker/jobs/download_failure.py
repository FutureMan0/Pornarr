"""Recover requests after a download client reports a failed job."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from pornarr_db.downloads import block_release
from pornarr_db.models.download import DownloadJob
from pornarr_db.models.notification import NotificationKind
from pornarr_db.models.request import Request, RequestStatus
from pornarr_db.notifications import notify_administrators, notify_user
from pornarr_db.requests import transition_request
from pornarr_shared.jobs import INDEXER_QUEUE, enqueue_once
from pornarr_worker.jobs.request_search import REQUEST_SEARCH_JOB

MAX_AUTOMATIC_DOWNLOAD_RETRIES = 2
TEMPORARY_BLOCK_DURATION = timedelta(minutes=15)
PERMANENT_BLOCK_DURATION = timedelta(days=7)
_PERMANENT_FAILURE_MARKERS = (
    "authentication",
    "corrupt",
    "invalid",
    "malformed",
    "missing",
    "not found",
    "password",
    "unsupported",
)


def is_permanent_failure(reason: str) -> bool:
    """Only classify unambiguous release errors as permanent; retry unknown failures."""
    normalized = reason.casefold()
    return any(marker in normalized for marker in _PERMANENT_FAILURE_MARKERS)


async def handle_download_failure(session: AsyncSession, redis: Any, job: DownloadJob) -> str:
    """Block a failed release, then retry an alternate or notify after exhaustion."""
    reason = job.error or "The download client reported a failure."
    permanent = is_permanent_failure(reason)
    now = datetime.now(UTC)
    await block_release(
        session,
        job.release_guid,
        blocked_until=now + (PERMANENT_BLOCK_DURATION if permanent else TEMPORARY_BLOCK_DURATION),
        reason=reason,
    )
    request = await session.scalar(
        select(Request).where(Request.download_job_id == job.id).with_for_update()
    )
    if request is None or request.status not in {RequestStatus.QUEUED, RequestStatus.DOWNLOADING}:
        return "untracked"
    await transition_request(session, request, RequestStatus.FAILED)
    if permanent:
        await _notify_failure(session, redis, request, job, reason, permanent=True)
        return "permanent"
    if request.download_retry_attempts >= MAX_AUTOMATIC_DOWNLOAD_RETRIES:
        await _notify_failure(session, redis, request, job, reason, permanent=False)
        return "exhausted"
    request.download_retry_attempts += 1
    request.search_attempts += 1
    request.next_search_at = now
    await transition_request(session, request, RequestStatus.SEARCHING)
    await enqueue_once(
        redis,
        REQUEST_SEARCH_JOB.name,
        str(request.id),
        request.search_attempts,
        queue=INDEXER_QUEUE,
    )
    return "retrying"


async def _notify_failure(
    session: AsyncSession,
    redis: Any,
    request: Request,
    job: DownloadJob,
    reason: str,
    *,
    permanent: bool,
) -> None:
    payload: dict[str, object] = {
        "request_id": str(request.id),
        "release_guid": job.release_guid,
        "reason": reason,
        "retry_attempts": request.download_retry_attempts,
        "permanent": permanent,
    }
    await notify_user(
        session,
        redis,
        user_id=request.user_id,
        kind=NotificationKind.REQUEST_FAILED,
        payload=payload,
    )
    await notify_administrators(
        session,
        redis,
        kind=NotificationKind.INSTANCE_NOTICE,
        payload=payload,
    )
