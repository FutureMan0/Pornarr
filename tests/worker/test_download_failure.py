"""Recovery policy for failed download-client jobs."""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from datetime import timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from pornarr_db.base import Base
from pornarr_db.downloads import is_release_blocked
from pornarr_db.models.download import BlockedRelease, DownloadJob
from pornarr_db.models.download_client import DownloadClient
from pornarr_db.models.notification import Notification, NotificationKind
from pornarr_db.models.request import Request, RequestHistory, RequestStatus
from pornarr_db.models.user import User, UserRole
from pornarr_db.types import set_cipher
from pornarr_shared.crypto import CredentialCipher
from pornarr_worker.jobs.download_failure import handle_download_failure

SECRET = "0123456789abcdef0123456789abcdef"


@pytest.fixture(autouse=True)
def _cipher() -> Iterator[None]:
    set_cipher(CredentialCipher(SECRET))
    yield
    set_cipher(None)


@pytest.fixture
async def session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as created_session:
        yield created_session
    await engine.dispose()


class RecordingRedis:
    def __init__(self) -> None:
        self.enqueued: list[dict[str, object]] = []
        self.events: list[dict[str, object]] = []

    async def enqueue_job(self, function: str, *args: object, **kwargs: object) -> str:
        self.enqueued.append({"function": function, "args": args, **kwargs})
        return "queued"

    async def xadd(self, _: str, fields: dict[str, object]) -> str:
        self.events.append(fields)
        return "1-0"

    async def publish(self, _: str, __: str) -> None:
        return None


async def _request_with_failed_job(
    session: AsyncSession, *, error: str, retry_attempts: int = 0
) -> tuple[Request, DownloadJob, User]:
    user = User(username="requester", password_hash="hash")
    admin = User(username="admin", password_hash="hash", role=UserRole.ADMIN)
    client = DownloadClient(
        name="client",
        protocol="torrent",
        implementation="adapter",
        host="client.example",
        port=8080,
        credentials="secret",
    )
    session.add_all([user, admin, client])
    await session.flush()
    job = DownloadJob(
        download_client_id=client.id,
        client_name=client.name,
        protocol=client.protocol,
        release_guid="failed-release",
        client_job_id="failed-client-job",
        status="failed",
        error=error,
    )
    session.add(job)
    await session.flush()
    request = Request(
        user_id=user.id,
        query="Example",
        status=RequestStatus.QUEUED,
        download_job_id=job.id,
        download_retry_attempts=retry_attempts,
    )
    session.add(request)
    await session.flush()
    session.add(RequestHistory(request_id=request.id, status=RequestStatus.QUEUED))
    return request, job, admin


async def test_transient_failure_blocks_the_release_and_schedules_an_alternate(
    session: AsyncSession,
) -> None:
    request, job, _ = await _request_with_failed_job(session, error="connection timed out")
    redis = RecordingRedis()

    outcome = await handle_download_failure(session, redis, job)
    await session.commit()

    blocked = await session.scalar(select(BlockedRelease))
    history = list(
        await session.scalars(
            select(RequestHistory.status)
            .where(RequestHistory.request_id == request.id)
            .order_by(RequestHistory.created_at)
        )
    )
    assert outcome == "retrying"
    assert request.status is RequestStatus.SEARCHING
    assert request.download_retry_attempts == 1
    assert history == [RequestStatus.QUEUED, RequestStatus.FAILED, RequestStatus.SEARCHING]
    assert blocked is not None and blocked.reason == "connection timed out"
    assert await is_release_blocked(
        session, job.release_guid, now=blocked.blocked_until - timedelta(seconds=1)
    )
    assert not await is_release_blocked(
        session, job.release_guid, now=blocked.blocked_until + timedelta(seconds=1)
    )
    assert redis.enqueued[0]["function"] == "request_search"
    assert redis.enqueued[0]["args"] == (str(request.id), 1)


async def test_permanent_failure_stops_retrying_and_notifies_requester_and_admin(
    session: AsyncSession,
) -> None:
    request, job, admin = await _request_with_failed_job(session, error="invalid release payload")
    redis = RecordingRedis()

    outcome = await handle_download_failure(session, redis, job)
    await session.commit()

    notifications = list(await session.scalars(select(Notification).order_by(Notification.user_id)))
    assert outcome == "permanent"
    assert request.status is RequestStatus.FAILED
    assert request.download_retry_attempts == 0
    assert redis.enqueued == []
    assert {(notification.user_id, notification.kind) for notification in notifications} == {
        (request.user_id, NotificationKind.REQUEST_FAILED),
        (admin.id, NotificationKind.INSTANCE_NOTICE),
    }
    assert all(
        notification.payload["reason"] == "invalid release payload"
        for notification in notifications
    )


async def test_retry_exhaustion_notifies_without_scheduling_a_third_attempt(
    session: AsyncSession,
) -> None:
    request, job, _ = await _request_with_failed_job(
        session, error="connection timed out", retry_attempts=2
    )
    redis = RecordingRedis()

    outcome = await handle_download_failure(session, redis, job)
    await session.commit()

    notifications = list(await session.scalars(select(Notification)))
    assert outcome == "exhausted"
    assert request.status is RequestStatus.FAILED
    assert request.download_retry_attempts == 2
    assert redis.enqueued == []
    assert len(notifications) == 2
    assert all(notification.payload["retry_attempts"] == 2 for notification in notifications)
