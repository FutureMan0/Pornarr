"""Request lifecycle transitions."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from pornarr_db.models.request import Request, RequestHistory, RequestStatus


class InvalidRequestTransitionError(ValueError):
    """Raised when a request cannot move to the requested lifecycle state."""


_ALLOWED_TRANSITIONS = {
    RequestStatus.SEARCHING: frozenset(
        {
            RequestStatus.RESULTS_FOUND,
            RequestStatus.NOT_FOUND,
            RequestStatus.MONITORING,
            RequestStatus.FAILED,
            RequestStatus.CANCELLED,
        }
    ),
    RequestStatus.RESULTS_FOUND: frozenset(
        {RequestStatus.SEARCHING, RequestStatus.QUEUED, RequestStatus.CANCELLED}
    ),
    RequestStatus.QUEUED: frozenset(
        {RequestStatus.DOWNLOADING, RequestStatus.FAILED, RequestStatus.CANCELLED}
    ),
    RequestStatus.DOWNLOADING: frozenset(
        {RequestStatus.PROCESSING, RequestStatus.FAILED, RequestStatus.CANCELLED}
    ),
    RequestStatus.PROCESSING: frozenset({RequestStatus.AVAILABLE, RequestStatus.FAILED}),
    RequestStatus.FAILED: frozenset({RequestStatus.SEARCHING, RequestStatus.CANCELLED}),
    RequestStatus.NOT_FOUND: frozenset(
        {RequestStatus.SEARCHING, RequestStatus.MONITORING, RequestStatus.CANCELLED}
    ),
    RequestStatus.MONITORING: frozenset(
        {RequestStatus.SEARCHING, RequestStatus.NOT_FOUND, RequestStatus.CANCELLED}
    ),
    RequestStatus.AVAILABLE: frozenset(),
    RequestStatus.CANCELLED: frozenset(),
}


async def transition_request(
    session: AsyncSession, request: Request, status: RequestStatus
) -> RequestHistory:
    """Move a request through its legal lifecycle and record both states once."""
    if status not in _ALLOWED_TRANSITIONS[request.status]:
        raise InvalidRequestTransitionError(
            f"Cannot move request from {request.status} to {status}."
        )

    has_history = await session.scalar(
        select(RequestHistory.id).where(RequestHistory.request_id == request.id).limit(1)
    )
    if has_history is None:
        session.add(RequestHistory(request_id=request.id, status=request.status))
    request.status = status
    history = RequestHistory(request_id=request.id, status=status)
    session.add(history)
    return history


async def advance_requests_for_download(
    session: AsyncSession, download_job_id: UUID, status: RequestStatus
) -> list[Request]:
    """Move every request grabbed into this download job one step further.

    Grabbing something the client already has is the same job (defect 10), so
    more than one request can point at `download_job_id`, each at whatever
    stage it is honestly in - one may already be cancelled while another is
    still queued. Only a request for which `status` is the *next* legal step
    is moved; every other one, including a request already at or past
    `status`, is left alone rather than raised on. Callers that need a request
    to walk more than one step call this once per step, in order.
    """
    requests = list(
        await session.scalars(select(Request).where(Request.download_job_id == download_job_id))
    )
    moved = []
    for request in requests:
        if status in _ALLOWED_TRANSITIONS.get(request.status, frozenset()):
            await transition_request(session, request, status)
            moved.append(request)
    return moved
