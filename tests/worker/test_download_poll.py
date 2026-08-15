"""Download-client polling state transitions."""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from pornarr_db.base import Base
from pornarr_db.models.download import DownloadHistory, DownloadJob
from pornarr_db.models.download_client import DownloadClient
from pornarr_db.types import set_cipher
from pornarr_integrations.downloaders import DownloadClientJob, DownloadState
from pornarr_shared.crypto import CredentialCipher
from pornarr_worker.jobs.download_poll import poll_downloads

SECRET = "0123456789abcdef0123456789abcdef"


class PollingAdapter:
    def __init__(self, result: list[DownloadClientJob] | Exception) -> None:
        self.result = result
        self.calls: list[dict[str, object]] = []

    async def list_jobs(
        self, *, host: str, port: int, url_base: str, credentials: str
    ) -> list[DownloadClientJob]:
        self.calls.append(
            {
                "host": host,
                "port": port,
                "url_base": url_base,
                "credentials": credentials,
            }
        )
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


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


async def test_poll_batches_each_client_and_isolates_an_unreachable_client(
    session: AsyncSession,
) -> None:
    healthy = DownloadClient(
        name="healthy",
        protocol="torrent",
        implementation="healthy-adapter",
        host="healthy.example",
        port=8080,
        credentials="healthy-secret",
        health="unknown",
    )
    unreachable = DownloadClient(
        name="unreachable",
        protocol="usenet",
        implementation="unreachable-adapter",
        host="unreachable.example",
        port=8081,
        credentials="unreachable-secret",
        health="unknown",
    )
    session.add_all([healthy, unreachable])
    await session.flush()
    completed = DownloadJob(
        download_client_id=healthy.id,
        client_name=healthy.name,
        protocol=healthy.protocol,
        release_guid="completed-release",
        client_job_id="completed-client-job",
        status="downloading",
    )
    still_running = DownloadJob(
        download_client_id=unreachable.id,
        client_name=unreachable.name,
        protocol=unreachable.protocol,
        release_guid="unreachable-release",
        client_job_id="unreachable-client-job",
        status="downloading",
    )
    session.add_all([completed, still_running])
    await session.commit()
    assert healthy.enabled and unreachable.enabled

    healthy_adapter = PollingAdapter(
        [
            DownloadClientJob(
                client_job_id="completed-client-job",
                state=DownloadState.COMPLETED,
                size_bytes=100,
                remaining_bytes=0,
                download_speed_bytes=0,
                estimated_seconds=0,
                output_path="/data/torrents/completed-release",
            )
        ]
    )
    unreachable_adapter = PollingAdapter(
        ConnectionError("client unavailable with unreachable-secret")
    )
    events: list[tuple[str, dict[str, Any]]] = []
    imports: list[tuple[str, str | None]] = []

    async def publish(event_type: str, data: dict[str, Any]) -> None:
        events.append((event_type, data))

    async def enqueue_import(job: DownloadJob, output_path: str | None) -> None:
        imports.append((str(job.id), output_path))

    await poll_downloads(
        session,
        adapters={
            "healthy-adapter": healthy_adapter,
            "unreachable-adapter": unreachable_adapter,
        },
        publish=publish,
        enqueue_import=enqueue_import,
    )
    await session.commit()

    assert len(healthy_adapter.calls) == 1
    assert len(unreachable_adapter.calls) == 1
    assert completed.status == "completed"
    assert (completed.size_bytes, completed.remaining_bytes, completed.estimated_seconds) == (
        100,
        0,
        0,
    )
    assert healthy.health == "healthy"
    assert unreachable.health == "unhealthy"
    assert unreachable.last_error == "client unavailable with [redacted]"
    assert still_running.status == "downloading"
    assert imports == [(str(completed.id), "/data/torrents/completed-release")]
    assert events == [("download.status", {"job_id": str(completed.id), "status": "completed"})]
    history = await session.scalar(
        select(DownloadHistory).where(DownloadHistory.download_job_id == completed.id)
    )
    assert history is not None and history.status == "completed"


async def test_poll_marks_a_client_removed_job_without_enqueuing_import(
    session: AsyncSession,
) -> None:
    client = DownloadClient(
        name="client",
        protocol="torrent",
        implementation="adapter",
        host="client.example",
        port=8080,
        credentials="secret",
    )
    session.add(client)
    await session.flush()
    job = DownloadJob(
        download_client_id=client.id,
        client_name=client.name,
        protocol=client.protocol,
        release_guid="gone-release",
        client_job_id="gone-client-job",
        status="downloading",
    )
    session.add(job)
    await session.commit()
    assert client.enabled
    adapter = PollingAdapter([])
    events: list[tuple[str, dict[str, Any]]] = []

    async def publish(event_type: str, data: dict[str, Any]) -> None:
        events.append((event_type, data))

    async def enqueue_import(_: DownloadJob, __: str | None) -> None:
        pytest.fail("a removed job must not be imported")

    await poll_downloads(
        session,
        adapters={"adapter": adapter},
        publish=publish,
        enqueue_import=enqueue_import,
    )
    await session.commit()

    assert job.status == "removed"
    assert job.error == "Job no longer exists in the download client."
    assert events == [("download.status", {"job_id": str(job.id), "status": "removed"})]


async def test_poll_replays_a_persisted_completion_after_a_worker_restart(
    session: AsyncSession,
) -> None:
    client = DownloadClient(
        name="client",
        protocol="torrent",
        implementation="adapter",
        host="client.example",
        port=8080,
        credentials="secret",
    )
    session.add(client)
    await session.flush()
    completed = DownloadJob(
        download_client_id=client.id,
        client_name=client.name,
        protocol=client.protocol,
        release_guid="release",
        client_job_id="client-job",
        status="completed",
    )
    session.add(completed)
    await session.commit()
    adapter = PollingAdapter(
        [
            DownloadClientJob(
                client_job_id="client-job",
                state=DownloadState.COMPLETED,
                size_bytes=100,
                remaining_bytes=0,
                download_speed_bytes=0,
                estimated_seconds=0,
                output_path="/data/torrents/release",
            )
        ]
    )
    imports: list[tuple[str, str | None]] = []
    events: list[str] = []

    async def publish(event_type: str, _: dict[str, Any]) -> None:
        events.append(event_type)

    async def enqueue_import(job: DownloadJob, output_path: str | None) -> None:
        imports.append((str(job.id), output_path))

    await poll_downloads(
        session,
        adapters={"adapter": adapter},
        publish=publish,
        enqueue_import=enqueue_import,
    )

    assert imports == [(str(completed.id), "/data/torrents/release")]
    assert events == ["download.progress"]


async def test_polling_one_hundred_jobs_makes_one_client_request(session: AsyncSession) -> None:
    client = DownloadClient(
        name="client",
        protocol="torrent",
        implementation="adapter",
        host="client.example",
        port=8080,
        credentials="secret",
    )
    session.add(client)
    await session.flush()
    session.add_all(
        [
            DownloadJob(
                download_client_id=client.id,
                client_name=client.name,
                protocol=client.protocol,
                release_guid=f"release-{number}",
                client_job_id=f"client-job-{number}",
                status="downloading",
            )
            for number in range(100)
        ]
    )
    await session.commit()
    adapter = PollingAdapter(
        [
            DownloadClientJob(
                client_job_id=f"client-job-{number}",
                state=DownloadState.DOWNLOADING,
                size_bytes=100,
                remaining_bytes=50,
                download_speed_bytes=10,
                estimated_seconds=5,
            )
            for number in range(100)
        ]
    )

    events: list[tuple[str, dict[str, Any]]] = []

    async def publish(event_type: str, data: dict[str, Any]) -> None:
        events.append((event_type, data))

    async def enqueue_import(_: DownloadJob, __: str | None) -> None:
        pytest.fail("running jobs must not be imported")

    await poll_downloads(
        session,
        adapters={"adapter": adapter},
        publish=publish,
        enqueue_import=enqueue_import,
    )

    assert len(adapter.calls) == 1
    assert len(events) == 100
    assert {event_type for event_type, _ in events} == {"download.progress"}


async def test_poll_records_failure_without_collapsing_a_stalled_job(session: AsyncSession) -> None:
    client = DownloadClient(
        name="client",
        protocol="torrent",
        implementation="adapter",
        host="client.example",
        port=8080,
        credentials="secret",
    )
    session.add(client)
    await session.flush()
    failed = DownloadJob(
        download_client_id=client.id,
        client_name=client.name,
        protocol=client.protocol,
        release_guid="failed-release",
        client_job_id="failed-client-job",
        status="downloading",
    )
    stalled = DownloadJob(
        download_client_id=client.id,
        client_name=client.name,
        protocol=client.protocol,
        release_guid="stalled-release",
        client_job_id="stalled-client-job",
        status="downloading",
    )
    session.add_all([failed, stalled])
    await session.commit()
    adapter = PollingAdapter(
        [
            DownloadClientJob(
                client_job_id="failed-client-job",
                state=DownloadState.FAILED,
                size_bytes=100,
                remaining_bytes=50,
                download_speed_bytes=0,
                estimated_seconds=None,
                error="client error",
            ),
            DownloadClientJob(
                client_job_id="stalled-client-job",
                state=DownloadState.STALLED,
                size_bytes=100,
                remaining_bytes=50,
                download_speed_bytes=0,
                estimated_seconds=None,
            ),
        ]
    )
    events: list[tuple[str, dict[str, Any]]] = []

    async def publish(event_type: str, data: dict[str, Any]) -> None:
        events.append((event_type, data))

    async def enqueue_import(_: DownloadJob, __: str | None) -> None:
        pytest.fail("failed and stalled jobs must not be imported")

    handled: list[DownloadJob] = []

    async def handle_failure(job: DownloadJob) -> None:
        handled.append(job)

    await poll_downloads(
        session,
        adapters={"adapter": adapter},
        publish=publish,
        enqueue_import=enqueue_import,
        handle_failure=handle_failure,
    )
    await session.commit()

    assert (failed.status, failed.error) == ("failed", "client error")
    assert stalled.status == "stalled"
    assert handled == [failed]
    assert events == [
        ("download.status", {"job_id": str(failed.id), "status": "failed"}),
        ("download.status", {"job_id": str(stalled.id), "status": "stalled"}),
    ]
    history = await session.scalar(
        select(DownloadHistory).where(DownloadHistory.download_job_id == failed.id)
    )
    assert history is not None and history.error == "client error"
