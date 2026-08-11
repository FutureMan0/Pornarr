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
from pornarr_worker.artwork import ARTWORK_JOB, LIBRARY_ARTWORK_JOB
from pornarr_worker.cleanup import cleanup_transcodes
from pornarr_worker.jobs.automation import AUTOMATION_EXECUTION_JOB
from pornarr_worker.jobs.download_poll import DOWNLOAD_POLL_JOB
from pornarr_worker.jobs.events import PRUNE_USER_EVENTS_JOB
from pornarr_worker.jobs.profile import REFRESH_INTEREST_PROFILES_JOB
from pornarr_worker.jobs.scan import SCAN_JOB
from pornarr_worker.jobs.storage import REFRESH_STORAGE_JOB
from pornarr_worker.search import RELEASE_CACHE_CLEANUP_JOB, SEARCH_INDEXERS_JOB
from pornarr_worker.sprites import SPRITE_JOB

REDIS_SETTINGS = RedisSettings.from_dsn(get_settings().redis_url)


async def heartbeat(_: dict[str, Any]) -> str:
    """Small scheduled job proving that the scheduler and default queue work."""

    return "ok"


HEARTBEAT_JOB = job(heartbeat)
CLEANUP_TRANSCODES_JOB = job(cleanup_transcodes)


class WorkerSettings:
    """Default queue worker; ARQ settings are deliberately class attributes."""

    functions: ClassVar = [
        HEARTBEAT_JOB,
        CLEANUP_TRANSCODES_JOB,
        DOWNLOAD_POLL_JOB,
        AUTOMATION_EXECUTION_JOB,
        REFRESH_STORAGE_JOB,
        PRUNE_USER_EVENTS_JOB,
        REFRESH_INTEREST_PROFILES_JOB,
    ]
    queue_name: ClassVar = DEFAULT_QUEUE
    redis_settings: ClassVar = REDIS_SETTINGS
    job_timeout: ClassVar = JOB_TIMEOUT_SECONDS
    max_tries: ClassVar = JOB_MAX_TRIES
    retry_jobs: ClassVar = True
    job_completion_wait: ClassVar = JOB_COMPLETION_WAIT_SECONDS
    health_check_interval: ClassVar = 30


class ImportWorkerSettings:
    """Worker dedicated to slow import and filesystem work."""

    functions: ClassVar = [*WorkerSettings.functions, SCAN_JOB]
    queue_name: ClassVar = IMPORT_QUEUE
    redis_settings: ClassVar = REDIS_SETTINGS
    job_timeout: ClassVar = JOB_TIMEOUT_SECONDS
    max_tries: ClassVar = JOB_MAX_TRIES
    retry_jobs: ClassVar = True
    job_completion_wait: ClassVar = JOB_COMPLETION_WAIT_SECONDS
    health_check_interval: ClassVar = 30


class TranscodeWorkerSettings:
    """Worker dedicated to FFmpeg work so it cannot starve other queues."""

    functions: ClassVar = [SPRITE_JOB, ARTWORK_JOB, LIBRARY_ARTWORK_JOB]
    queue_name: ClassVar = TRANSCODE_QUEUE
    redis_settings: ClassVar = REDIS_SETTINGS
    job_timeout: ClassVar = JOB_TIMEOUT_SECONDS
    max_tries: ClassVar = JOB_MAX_TRIES
    retry_jobs: ClassVar = True
    job_completion_wait: ClassVar = JOB_COMPLETION_WAIT_SECONDS
    health_check_interval: ClassVar = 30


class IndexerWorkerSettings:
    """Worker dedicated to indexer and feed work."""

    functions: ClassVar = [SEARCH_INDEXERS_JOB, RELEASE_CACHE_CLEANUP_JOB]
    queue_name: ClassVar = INDEXER_QUEUE
    redis_settings: ClassVar = REDIS_SETTINGS
    job_timeout: ClassVar = JOB_TIMEOUT_SECONDS
    max_tries: ClassVar = JOB_MAX_TRIES
    retry_jobs: ClassVar = True
    job_completion_wait: ClassVar = JOB_COMPLETION_WAIT_SECONDS
    health_check_interval: ClassVar = 30


class SchedulerSettings:
    """Built-in ARQ cron scheduler, publishing periodic default-queue work."""

    functions: ClassVar = WorkerSettings.functions
    queue_name: ClassVar = DEFAULT_QUEUE
    redis_settings: ClassVar = REDIS_SETTINGS
    job_timeout: ClassVar = JOB_TIMEOUT_SECONDS
    max_tries: ClassVar = JOB_MAX_TRIES
    retry_jobs: ClassVar = True
    job_completion_wait: ClassVar = JOB_COMPLETION_WAIT_SECONDS
    health_check_interval: ClassVar = 30
    cron_jobs: ClassVar = [
        cron(
            HEARTBEAT_JOB.coroutine,
            name=HEARTBEAT_JOB.name,
            second=0,
            run_at_startup=True,
            max_tries=JOB_MAX_TRIES,
        ),
        cron(
            CLEANUP_TRANSCODES_JOB.coroutine,
            name=CLEANUP_TRANSCODES_JOB.name,
            hour=3,
            minute=0,
            max_tries=JOB_MAX_TRIES,
        ),
        cron(
            DOWNLOAD_POLL_JOB.coroutine,
            name=DOWNLOAD_POLL_JOB.name,
            second=set(range(0, 60, 5)),
            run_at_startup=True,
            max_tries=JOB_MAX_TRIES,
        ),
        cron(
            REFRESH_STORAGE_JOB.coroutine,
            name=REFRESH_STORAGE_JOB.name,
            minute={0},
            run_at_startup=True,
            max_tries=JOB_MAX_TRIES,
        ),
        cron(
            PRUNE_USER_EVENTS_JOB.coroutine,
            name=PRUNE_USER_EVENTS_JOB.name,
            hour=3,
            minute=30,
            max_tries=JOB_MAX_TRIES,
        ),
        cron(
            REFRESH_INTEREST_PROFILES_JOB.coroutine,
            name=REFRESH_INTEREST_PROFILES_JOB.name,
            hour=2,
            minute=30,
            max_tries=JOB_MAX_TRIES,
        ),
    ]
