"""Request lifecycle invariants."""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from pornarr_db.base import Base
from pornarr_db.models.request import Request, RequestHistory, RequestStatus
from pornarr_db.models.user import User
from pornarr_db.requests import InvalidRequestTransitionError, transition_request


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
