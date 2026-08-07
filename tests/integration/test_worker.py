"""ARQ behaviour that only holds against a real Redis server."""

from __future__ import annotations

import asyncio
import os
import signal
from uuid import uuid4

import pytest
from arq import Worker
from arq.connections import RedisSettings, create_pool
from arq.worker import func

import pornarr_shared.jobs as jobs
from pornarr_shared.jobs import enqueue_once, job

pytestmark = pytest.mark.integration


@pytest.fixture
async def redis_pool():
    redis_url = os.environ.get("REDIS_URL")
    if not redis_url:
        pytest.skip("REDIS_URL is required for worker integration tests")

    pool = await create_pool(RedisSettings.from_dsn(redis_url))
    try:
        yield pool
    finally:
        await pool.aclose()


async def test_worker_processes_one_copy_of_a_duplicate_job(redis_pool) -> None:
    executions: list[str] = []
    queue_name = f"pornarr:test:{uuid4().hex}"

    async def test_job(_: dict[str, object], media_id: str) -> None:
        executions.append(media_id)

    registered = func(test_job, name="test_job")
    first = await enqueue_once(redis_pool, registered.name, "media-1", queue=queue_name)
    duplicate = await enqueue_once(redis_pool, registered.name, "media-1", queue=queue_name)
    worker = Worker(
        functions=[registered],
        queue_name=queue_name,
        redis_pool=redis_pool,
        burst=True,
        handle_signals=False,
        poll_delay=0.01,
    )

    await worker.async_run()

    assert first is not None
    assert duplicate is None
    assert executions == ["media-1"]


async def test_timeout_is_retried(redis_pool, monkeypatch) -> None:
    attempts: list[int] = []
    queue_name = f"pornarr:test:{uuid4().hex}"
    monkeypatch.setattr(jobs, "RETRY_BACKOFF_BASE_SECONDS", 0.01)

    async def slow_job(context: dict[str, object]) -> None:
        attempt = context["job_try"]
        assert isinstance(attempt, int)
        attempts.append(attempt)
        await asyncio.sleep(0.1)

    registered = job(slow_job, timeout=0.01, max_tries=2)
    await enqueue_once(redis_pool, registered.name, queue=queue_name)
    worker = Worker(
        functions=[registered],
        queue_name=queue_name,
        redis_pool=redis_pool,
        burst=True,
        handle_signals=False,
        max_burst_jobs=3,
        poll_delay=0.01,
    )

    await worker.async_run()

    assert attempts == [1, 2]
    assert worker.jobs_retried == 2


async def test_sigterm_waits_for_an_in_flight_job(redis_pool) -> None:
    started = asyncio.Event()
    completed = asyncio.Event()
    queue_name = f"pornarr:test:{uuid4().hex}"

    async def draining_job(_: dict[str, object]) -> None:
        started.set()
        await asyncio.sleep(0.05)
        completed.set()

    registered = func(draining_job, name="draining_job")
    await enqueue_once(redis_pool, registered.name, queue=queue_name)
    worker = Worker(
        functions=[registered],
        queue_name=queue_name,
        redis_pool=redis_pool,
        handle_signals=False,
        job_completion_wait=1,
        poll_delay=0.01,
    )
    runner = asyncio.create_task(worker.async_run())

    await asyncio.wait_for(started.wait(), timeout=1)
    worker.handle_sig_wait_for_completion(signal.SIGTERM)

    with pytest.raises(asyncio.CancelledError):
        await runner

    assert worker.allow_pick_jobs is False
    assert completed.is_set()
