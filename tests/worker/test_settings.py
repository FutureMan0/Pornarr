"""Worker settings are importable by the ARQ CLI and expose all queues."""

from __future__ import annotations

import importlib
import sys

from pornarr_shared.config import get_settings
from pornarr_shared.jobs import (
    BACKLOG_SEARCH_JOB_NAME,
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
        "watch_download_files",
        "automation_execute",
        "refresh_storage",
        "prune_user_events_job",
        "prune_quarantine_job",
        "refresh_interest_profiles_job",
        "refresh_recommendations_job",
        "dispatch_due_request_searches",
        "dispatch_rss_sync",
        "dispatch_backlog_searches",
        "dispatch_perceptual_hashes",
        "generate_automatic_shorts_job",
    ]
    assert [job.name for job in settings.TranscodeWorkerSettings.functions] == [
        "generate_preview_sprite_job",
        "generate_preview_and_scenes_job",
        "generate_artwork_job",
        "regenerate_library_artwork_job",
        "generate_perceptual_hash_job",
        "probe_media_file_job",
    ]
    assert [job.name for job in settings.IndexerWorkerSettings.functions] == [
        "search_indexers",
        "cleanup_release_cache",
        "request_search",
        "rss_sync",
        "monitor_match",
        "backlog_search",
    ]
    assert settings.IndexerWorkerSettings.functions[-1].name == BACKLOG_SEARCH_JOB_NAME
    assert [function.name for function in settings.ImportWorkerSettings.functions] == [
        "heartbeat",
        "cleanup_transcodes",
        "download_poll",
        "watch_download_files",
        "automation_execute",
        "refresh_storage",
        "prune_user_events_job",
        "prune_quarantine_job",
        "refresh_interest_profiles_job",
        "refresh_recommendations_job",
        "dispatch_due_request_searches",
        "dispatch_rss_sync",
        "dispatch_backlog_searches",
        "dispatch_perceptual_hashes",
        "generate_automatic_shorts_job",
        "scan",
        "quarantine_job",
        "upgrade_media_file_job",
        "import_download",
        "import_media",
        "resolve_metadata_job",
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
        "watch_download_files",
        "dispatch_perceptual_hashes",
        "refresh_storage",
        "prune_user_events_job",
        "prune_quarantine_job",
        "refresh_interest_profiles_job",
        "refresh_recommendations_job",
        "dispatch_due_request_searches",
        "dispatch_rss_sync",
        "dispatch_backlog_searches",
        "generate_automatic_shorts_job",
    ]
    assert (
        cron_jobs["generate_automatic_shorts_job"].hour,
        cron_jobs["generate_automatic_shorts_job"].minute,
    ) == (3, 15)
    assert cron_jobs["download_poll"].second == set(range(0, 60, 5))
    assert cron_jobs["watch_download_files"].second == {0, 30}
    assert cron_jobs["dispatch_rss_sync"].minute == {0, 15, 30, 45}
    assert cron_jobs["dispatch_backlog_searches"].minute == set(range(0, 60))


def test_the_scheduler_publishes_cron_work_without_consuming_the_queue(monkeypatch) -> None:
    """ADR 0021 L9: one built-in cron, and one reader for the queue it fills.

    ARQ publishes a cron job by enqueueing it onto the scheduler's own
    `queue_name`, so the scheduler and the default worker necessarily share
    `pornarr:default`. Sharing it as a second consumer loses jobs between the
    two readers, and a lost `dispatch_rss_sync` or `download_poll` is silent.
    `max_jobs = 0` makes `_poll_iteration` skip its read - the guard is
    `job_counter < max_jobs` - while `heart_beat`, which runs the cron table,
    is outside that guard.
    """

    monkeypatch.setenv("APP_SECRET", "a" * 32)
    settings = importlib.import_module("pornarr_worker.settings")

    assert settings.SchedulerSettings.max_jobs == 0
    assert settings.SchedulerSettings.queue_name == settings.WorkerSettings.queue_name
    assert getattr(settings.WorkerSettings, "cron_jobs", None) is None
    assert getattr(settings.WorkerSettings, "max_jobs", None) != 0
