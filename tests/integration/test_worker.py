"""ARQ behaviour that only holds against a real Redis server."""

from __future__ import annotations

import asyncio
import os
import signal
import time
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

import pytest
from arq import Worker
from arq.connections import RedisSettings, create_pool
from arq.worker import func

import pornarr_shared.jobs as jobs
from pornarr_media.sessions import TranscodeSessionRegistry
from pornarr_shared.config import Settings
from pornarr_shared.jobs import enqueue_once, job

pytestmark = pytest.mark.integration


@dataclass
class _FakeProcess:
    pid: int = 1


@dataclass
class _FakeTranscode:
    process: _FakeProcess

    async def stop(self) -> None:
        return None


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


async def test_transcode_cleanup_job_reads_live_sessions_from_redis(
    redis_pool, monkeypatch, tmp_path: Path
) -> None:
    import pornarr_worker.cleanup as cleanup

    settings = Settings(
        app_secret="a" * 32,
        database_url="postgresql+asyncpg://pornarr:pornarr@localhost/pornarr",
        redis_url="redis://localhost:6379/7",
        data_path=tmp_path,
        transcode_cleanup_min_age_seconds=300,
        transcode_cache_max_gb=1,
    )
    monkeypatch.setattr(cleanup, "get_settings", lambda: settings)
    registry = TranscodeSessionRegistry(redis_pool, settings.transcode_path)
    active_id = uuid4()
    active_path = settings.transcode_path / str(active_id)
    active_path.mkdir(parents=True)
    await registry.register(active_id, uuid4(), uuid4(), "hls", _FakeTranscode(_FakeProcess()))
    orphan_path = settings.transcode_path / str(uuid4())
    orphan_path.mkdir()
    (orphan_path / "segment_00000.ts").write_bytes(b"orphan")
    old = time.time() - 600
    os.utime(orphan_path, (old, old))

    try:
        result = await cleanup.cleanup_transcodes({"redis": redis_pool})
        assert active_path.exists()
        assert not orphan_path.exists()
        assert result["removed_directories"] == 1
    finally:
        await registry.terminate(active_id)
