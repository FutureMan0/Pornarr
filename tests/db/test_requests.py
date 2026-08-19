"""Request lifecycle invariants."""

from __future__ import annotations

from collections.abc import AsyncIterator
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from pornarr_db.base import Base
from pornarr_db.models.request import Request, RequestHistory, RequestStatus
from pornarr_db.models.user import User
from pornarr_db.requests import (
    InvalidRequestTransitionError,
    advance_requests_for_download,
    transition_request,
)

LEGAL_TRANSITIONS = {
    RequestStatus.SEARCHING: {
        RequestStatus.RESULTS_FOUND,
        RequestStatus.NOT_FOUND,
        RequestStatus.MONITORING,
        RequestStatus.FAILED,
        RequestStatus.CANCELLED,
    },
    RequestStatus.RESULTS_FOUND: {
        RequestStatus.SEARCHING,
        RequestStatus.QUEUED,
        RequestStatus.CANCELLED,
    },
    RequestStatus.QUEUED: {
        RequestStatus.DOWNLOADING,
        RequestStatus.FAILED,
        RequestStatus.CANCELLED,
    },
    RequestStatus.DOWNLOADING: {
        RequestStatus.PROCESSING,
        RequestStatus.FAILED,
        RequestStatus.CANCELLED,
    },
    RequestStatus.PROCESSING: {RequestStatus.AVAILABLE, RequestStatus.FAILED},
    RequestStatus.FAILED: {RequestStatus.SEARCHING, RequestStatus.CANCELLED},
    RequestStatus.NOT_FOUND: {
        RequestStatus.SEARCHING,
        RequestStatus.MONITORING,
        RequestStatus.CANCELLED,
    },
    RequestStatus.MONITORING: {
        RequestStatus.SEARCHING,
        RequestStatus.NOT_FOUND,
        RequestStatus.CANCELLED,
    },
    RequestStatus.AVAILABLE: set(),
    RequestStatus.CANCELLED: set(),
}


async def _session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine("sqlite+aiosqlite://")
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        async with session_factory() as session:
            yield session
    finally:
        await engine.dispose()


async def test_transition_records_reconstructable_history() -> None:
    async for session in _session():
        user = User(username="requester", password_hash="not-a-real-password")
        session.add(user)
        await session.flush()
        request = Request(
            user_id=user.id, query="Example", status=RequestStatus.SEARCHING, priority=50
        )
        session.add(request)
        await session.flush()

        await transition_request(session, request, RequestStatus.RESULTS_FOUND)
        await transition_request(session, request, RequestStatus.QUEUED)
        await session.commit()

        history = list(
            await session.scalars(
                select(RequestHistory)
                .where(RequestHistory.request_id == request.id)
                .order_by(RequestHistory.created_at)
            )
        )

    assert request.status is RequestStatus.QUEUED
    assert [entry.status for entry in history] == [
        RequestStatus.SEARCHING,
        RequestStatus.RESULTS_FOUND,
        RequestStatus.QUEUED,
    ]


async def test_invalid_and_terminal_transitions_are_refused() -> None:
    async for session in _session():
        user = User(username="requester", password_hash="not-a-real-password")
        session.add(user)
        await session.flush()
        request = Request(
            user_id=user.id, query="Example", status=RequestStatus.AVAILABLE, priority=50
        )

        with pytest.raises(InvalidRequestTransitionError, match="available"):
            await transition_request(session, request, RequestStatus.SEARCHING)


async def test_every_request_transition_is_explicitly_allowed_or_refused() -> None:
    async for session in _session():
        user = User(username="requester", password_hash="not-a-real-password")
        session.add(user)
        await session.flush()
        for source, allowed_targets in LEGAL_TRANSITIONS.items():
            for target in RequestStatus:
                request = Request(user_id=user.id, query="Example", status=source, priority=50)
                session.add(request)
                await session.flush()

                if target in allowed_targets:
                    history = await transition_request(session, request, target)
                    assert request.status is target
                    assert history.status is target
                else:
                    with pytest.raises(InvalidRequestTransitionError):
                        await transition_request(session, request, target)


async def test_history_written_in_one_transaction_cannot_be_ordered_by_its_timestamps() -> None:
    """The record of *which state came first* is not recoverable from the rows.

    `RequestHistory.created_at` defaults to `func.now()`. In PostgreSQL that is
    `transaction_timestamp()`, so every row a single transaction writes carries
    the identical value; in SQLite it is `CURRENT_TIMESTAMP`, which has
    one-second resolution and ties for the same reason. A grab writes
    `results_found` and `queued` in one transaction
    (`_attach_grabbed_release`, apps/api/pornarr_api/routers/requests.py:499),
    so `order_by(RequestHistory.created_at)` cannot tell them apart - and
    `list_requests` does not even ask for an order. Recorded here rather than
    fixed: see .gauntlet/pieces/04-acquisition/BUILD.md defect 9.
    """
    async for session in _session():
        user = User(username="requester", password_hash="not-a-real-password")
        session.add(user)
        await session.flush()
        request = Request(
            user_id=user.id, query="Example", status=RequestStatus.RESULTS_FOUND, priority=50
        )
        session.add(request)
        await session.flush()

        await transition_request(session, request, RequestStatus.QUEUED)
        await session.commit()

        entries = list(
            await session.scalars(
                select(RequestHistory).where(RequestHistory.request_id == request.id)
            )
        )

    assert {entry.status for entry in entries} == {
        RequestStatus.RESULTS_FOUND,
        RequestStatus.QUEUED,
    }
    assert entries[0].created_at == entries[1].created_at


async def test_advance_requests_for_download_moves_only_the_eligible_ones() -> None:
    """Grabbing something the client already has is the same job (defect 10):
    more than one request can point at one `download_job_id`, each honestly
    wherever it is in its own lifecycle. Only the one directly one step behind
    `status` moves; a request already past it, already terminal, or more than
    one step behind is left exactly where it is - never raised on.
    """
    async for session in _session():
        user = User(username="requester", password_hash="not-a-real-password")
        session.add(user)
        await session.flush()
        job_id = uuid4()
        one_step_behind = Request(
            user_id=user.id,
            query="Example",
            status=RequestStatus.QUEUED,
            priority=50,
            download_job_id=job_id,
        )
        already_there = Request(
            user_id=user.id,
            query="Example",
            status=RequestStatus.DOWNLOADING,
            priority=50,
            download_job_id=job_id,
        )
        cancelled = Request(
            user_id=user.id,
            query="Example",
            status=RequestStatus.CANCELLED,
            priority=50,
            download_job_id=job_id,
        )
        unrelated = Request(
            user_id=user.id,
            query="Other",
            status=RequestStatus.QUEUED,
            priority=50,
            download_job_id=uuid4(),
        )
        session.add_all([one_step_behind, already_there, cancelled, unrelated])
        await session.flush()

        moved = await advance_requests_for_download(session, job_id, RequestStatus.DOWNLOADING)
        await session.commit()

    assert [request.id for request in moved] == [one_step_behind.id]
    assert one_step_behind.status is RequestStatus.DOWNLOADING
    assert already_there.status is RequestStatus.DOWNLOADING
    assert cancelled.status is RequestStatus.CANCELLED
    assert unrelated.status is RequestStatus.QUEUED
