"""ARQ worker settings loaded by the image role dispatcher."""

from __future__ import annotations

from typing import Any, ClassVar

from arq import cron
from arq.connections import RedisSettings

from pornarr_shared.config import get_settings
from pornarr_shared.jobs import (
    DEFAULT_QUEUE,
    IMPORT_QUEUE,
    INDEXER_QUEUE,
    JOB_COMPLETION_WAIT_SECONDS,
    JOB_MAX_TRIES,
    JOB_TIMEOUT_SECONDS,
    TRANSCODE_QUEUE,
    job,
)

REDIS_SETTINGS = RedisSettings.from_dsn(get_settings().redis_url)


async def heartbeat(_: dict[str, Any]) -> str:
    """Small scheduled job proving that the scheduler and default queue work."""

    return "ok"


HEARTBEAT_JOB = job(heartbeat)


class WorkerSettings:
    """Default queue worker; ARQ settings are deliberately class attributes."""

    functions: ClassVar = [HEARTBEAT_JOB]
    queue_name: ClassVar = DEFAULT_QUEUE
    redis_settings: ClassVar = REDIS_SETTINGS
    job_timeout: ClassVar = JOB_TIMEOUT_SECONDS
    max_tries: ClassVar = JOB_MAX_TRIES
    retry_jobs: ClassVar = True
    job_completion_wait: ClassVar = JOB_COMPLETION_WAIT_SECONDS


class ImportWorkerSettings:
    """Worker dedicated to slow import and filesystem work."""

    functions: ClassVar = WorkerSettings.functions
    queue_name: ClassVar = IMPORT_QUEUE
    redis_settings: ClassVar = REDIS_SETTINGS
    job_timeout: ClassVar = JOB_TIMEOUT_SECONDS
    max_tries: ClassVar = JOB_MAX_TRIES
    retry_jobs: ClassVar = True
    job_completion_wait: ClassVar = JOB_COMPLETION_WAIT_SECONDS


class TranscodeWorkerSettings:
    """Worker dedicated to FFmpeg work so it cannot starve other queues."""

    functions: ClassVar = WorkerSettings.functions
    queue_name: ClassVar = TRANSCODE_QUEUE
    redis_settings: ClassVar = REDIS_SETTINGS
    job_timeout: ClassVar = JOB_TIMEOUT_SECONDS
    max_tries: ClassVar = JOB_MAX_TRIES
    retry_jobs: ClassVar = True
    job_completion_wait: ClassVar = JOB_COMPLETION_WAIT_SECONDS


class IndexerWorkerSettings:
    """Worker dedicated to indexer and feed work."""

    functions: ClassVar = WorkerSettings.functions
    queue_name: ClassVar = INDEXER_QUEUE
    redis_settings: ClassVar = REDIS_SETTINGS
    job_timeout: ClassVar = JOB_TIMEOUT_SECONDS
    max_tries: ClassVar = JOB_MAX_TRIES
    retry_jobs: ClassVar = True
    job_completion_wait: ClassVar = JOB_COMPLETION_WAIT_SECONDS


class SchedulerSettings:
    """Built-in ARQ cron scheduler, publishing periodic default-queue work."""

    functions: ClassVar = WorkerSettings.functions
    queue_name: ClassVar = DEFAULT_QUEUE
    redis_settings: ClassVar = REDIS_SETTINGS
    job_timeout: ClassVar = JOB_TIMEOUT_SECONDS
    max_tries: ClassVar = JOB_MAX_TRIES
    retry_jobs: ClassVar = True
    job_completion_wait: ClassVar = JOB_COMPLETION_WAIT_SECONDS
    cron_jobs: ClassVar = [
        cron(
            HEARTBEAT_JOB.coroutine,
            name=HEARTBEAT_JOB.name,
            second=0,
            run_at_startup=True,
            max_tries=JOB_MAX_TRIES,
        )
    ]
