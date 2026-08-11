"""Worker settings are importable by the ARQ CLI and expose all queues."""

from __future__ import annotations

import importlib
import sys

from pornarr_shared.config import get_settings
from pornarr_shared.jobs import (
    DEFAULT_QUEUE,
    IMPORT_QUEUE,
    INDEXER_QUEUE,
    JOB_COMPLETION_WAIT_SECONDS,
    TRANSCODE_QUEUE,
)


def test_worker_settings_register_all_queues(monkeypatch) -> None:
    monkeypatch.setenv("APP_SECRET", "a" * 32)
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://pornarr:pornarr@localhost/pornarr")
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/7")
    get_settings.cache_clear()
    sys.modules.pop("pornarr_worker.settings", None)
    settings = importlib.import_module("pornarr_worker.settings")
    worker_types = (
        settings.WorkerSettings,
        settings.ImportWorkerSettings,
        settings.TranscodeWorkerSettings,
        settings.IndexerWorkerSettings,
    )
    assert [worker.queue_name for worker in worker_types] == [
        DEFAULT_QUEUE,
        IMPORT_QUEUE,
        TRANSCODE_QUEUE,
        INDEXER_QUEUE,
    ]
    assert all(worker.job_completion_wait == JOB_COMPLETION_WAIT_SECONDS for worker in worker_types)
    assert [function.name for function in settings.WorkerSettings.functions] == [
        "heartbeat",
        "cleanup_transcodes",
        "download_poll",
        "automation_execute",
        "refresh_storage",
        "prune_user_events_job",
        "refresh_interest_profiles_job",
    ]
    assert [job.name for job in settings.TranscodeWorkerSettings.functions] == [
        "generate_preview_sprite_job",
        "generate_artwork_job",
        "regenerate_library_artwork_job",
    ]
    assert [job.name for job in settings.IndexerWorkerSettings.functions] == [
        "search_indexers",
        "cleanup_release_cache",
    ]
    assert [function.name for function in settings.ImportWorkerSettings.functions] == [
        "heartbeat",
        "cleanup_transcodes",
        "download_poll",
        "automation_execute",
        "refresh_storage",
        "prune_user_events_job",
        "refresh_interest_profiles_job",
        "scan",
    ]
    required_arq_options = {
        "functions",
        "queue_name",
        "redis_settings",
        "job_timeout",
        "max_tries",
        "retry_jobs",
        "job_completion_wait",
    }
    assert all(required_arq_options <= worker.__dict__.keys() for worker in worker_types)
    cron_jobs = {cron_job.name: cron_job for cron_job in settings.SchedulerSettings.cron_jobs}
    assert list(cron_jobs) == [
        "heartbeat",
        "cleanup_transcodes",
        "download_poll",
        "refresh_storage",
        "prune_user_events_job",
        "refresh_interest_profiles_job",
    ]
    assert cron_jobs["download_poll"].second == set(range(0, 60, 5))
