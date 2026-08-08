"""Request lifecycle transitions."""

from __future__ import annotations

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
    RequestStatus.MONITORING: frozenset({RequestStatus.SEARCHING, RequestStatus.CANCELLED}),
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
