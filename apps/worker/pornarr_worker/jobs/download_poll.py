"""Batched state synchronisation for configured download clients."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from pornarr_db.models.download import DownloadHistory, DownloadJob
from pornarr_db.models.download_client import DownloadClient
from pornarr_db.session import session_scope
from pornarr_integrations.downloaders import DownloadClientJob, DownloadClientPollingAdapter
from pornarr_integrations.qbittorrent import QbittorrentAdapter
from pornarr_integrations.sabnzbd import SabnzbdAdapter
from pornarr_shared.config import get_settings
from pornarr_shared.events import publish_event
from pornarr_shared.jobs import job
from pornarr_worker.jobs.download_failure import handle_download_failure
from pornarr_worker.jobs.import_trigger import (
    dispatch_committed_triggers,
    stage_completed_download,
)

TERMINAL_STATUSES = frozenset({"completed", "failed", "removed"})
NON_POLLABLE_STATUSES = frozenset({"failed", "removed"})
EventPublisher = Callable[[str, dict[str, Any]], Awaitable[None]]
ImportEnqueuer = Callable[[DownloadJob, str | None], Awaitable[None]]
FailureHandler = Callable[[DownloadJob], Awaitable[None]]

DOWNLOAD_CLIENT_ADAPTERS: Mapping[str, DownloadClientPollingAdapter] = {
    "qbittorrent": QbittorrentAdapter(),
    "sabnzbd": SabnzbdAdapter(),
}


async def poll_downloads(
    session: AsyncSession,
    *,
    adapters: Mapping[str, DownloadClientPollingAdapter],
    publish: EventPublisher,
    enqueue_import: ImportEnqueuer,
    handle_failure: FailureHandler | None = None,
) -> None:
    """Synchronise active jobs using one batched poll per configured client."""
    clients = list(
        await session.scalars(select(DownloadClient).where(DownloadClient.enabled.is_(True)))
    )
    client_ids = [client.id for client in clients]
    active_jobs = (
        list(
            await session.scalars(
                select(DownloadJob).where(
                    DownloadJob.download_client_id.in_(client_ids),
                    DownloadJob.status.not_in(NON_POLLABLE_STATUSES),
                )
            )
        )
        if client_ids
        else []
    )
    jobs_by_client: dict[object, list[DownloadJob]] = {client.id: [] for client in clients}
    for active_job in active_jobs:
        if active_job.download_client_id is not None:
            jobs_by_client[active_job.download_client_id].append(active_job)

    pollable: list[tuple[DownloadClient, DownloadClientPollingAdapter]] = []
    for client in clients:
        adapter = adapters.get(client.implementation)
        if adapter is None:
            client.health = "unhealthy"
            client.last_error = f"No polling adapter is registered for {client.implementation!r}."
            continue
        pollable.append((client, adapter))

    results = await asyncio.gather(
        *[_poll_client(client, adapter) for client, adapter in pollable], return_exceptions=True
    )
    for (client, _), result in zip(pollable, results, strict=True):
        if isinstance(result, asyncio.CancelledError):
            raise result
        if isinstance(result, BaseException):
            client.health = "unhealthy"
            client.last_error = _redact_client_error(str(result), client.credentials)
            continue
        client.health = "healthy"
        client.last_error = None
        client_jobs = {job.client_job_id: job for job in result}
        for active_job in jobs_by_client[client.id]:
            if active_job.client_job_id is None:
                continue
            polled_job = client_jobs.get(active_job.client_job_id)
            if polled_job is None:
                await _transition(
                    session,
                    active_job,
                    status="removed",
                    error="Job no longer exists in the download client.",
                    output_path=None,
                    publish=publish,
                    enqueue_import=enqueue_import,
                    handle_failure=handle_failure,
                )
            else:
                await _apply_poll(
                    session,
                    active_job,
                    polled_job,
                    publish=publish,
                    enqueue_import=enqueue_import,
                    handle_failure=handle_failure,
                )


async def _poll_client(
    client: DownloadClient, adapter: DownloadClientPollingAdapter
) -> list[DownloadClientJob]:
    return await adapter.list_jobs(
        host=client.host,
        port=client.port,
        url_base=client.url_base,
        credentials=client.credentials,
    )


def _redact_client_error(message: str, credentials: str) -> str:
    """Do not let an HTTP exception copy a client secret into persistent state."""
    return message.replace(credentials, "[redacted]")


async def _apply_poll(
    session: AsyncSession,
    job_row: DownloadJob,
    polled_job: DownloadClientJob,
    *,
    publish: EventPublisher,
    enqueue_import: ImportEnqueuer,
    handle_failure: FailureHandler | None,
) -> None:
    progress_changed = (
        job_row.size_bytes,
        job_row.remaining_bytes,
        job_row.download_speed_bytes,
        job_row.estimated_seconds,
    ) != (
        polled_job.size_bytes,
        polled_job.remaining_bytes,
        polled_job.download_speed_bytes,
        polled_job.estimated_seconds,
    )
    job_row.size_bytes = polled_job.size_bytes
    job_row.remaining_bytes = polled_job.remaining_bytes
    job_row.download_speed_bytes = polled_job.download_speed_bytes
    job_row.estimated_seconds = polled_job.estimated_seconds
    status_changed = await _transition(
        session,
        job_row,
        status=polled_job.state.value,
        error=polled_job.error,
        output_path=polled_job.output_path,
        publish=publish,
        enqueue_import=enqueue_import,
        handle_failure=handle_failure,
    )
    if progress_changed and not status_changed:
        await publish(
            "download.progress",
            {
                "job_id": str(job_row.id),
                "size_bytes": job_row.size_bytes,
                "remaining_bytes": job_row.remaining_bytes,
                "download_speed_bytes": job_row.download_speed_bytes,
                "estimated_seconds": job_row.estimated_seconds,
            },
        )


async def _transition(
    session: AsyncSession,
    job_row: DownloadJob,
    *,
    status: str,
    error: str | None,
    output_path: str | None,
    publish: EventPublisher,
    enqueue_import: ImportEnqueuer,
    handle_failure: FailureHandler | None,
) -> bool:
    changed = job_row.status != status
    job_row.status = status
    job_row.error = error
    if status in TERMINAL_STATUSES:
        await _record_history(session, job_row)
    if status == "completed":
        await enqueue_import(job_row, output_path)
    if not changed:
        return False
    await publish("download.status", {"job_id": str(job_row.id), "status": status})
    if status == "failed" and handle_failure is not None:
        await handle_failure(job_row)
    return True


async def _record_history(session: AsyncSession, job_row: DownloadJob) -> None:
    existing = await session.scalar(
        select(DownloadHistory.id).where(DownloadHistory.download_job_id == job_row.id)
    )
    if existing is not None:
        return
    session.add(
        DownloadHistory(
            download_job_id=job_row.id,
            client_name=job_row.client_name,
            client_job_id=job_row.client_job_id,
            protocol=job_row.protocol,
            release_guid=job_row.release_guid,
            status=job_row.status,
            size_bytes=job_row.size_bytes,
            error=job_row.error,
        )
    )


async def download_poll(context: dict[str, Any]) -> None:
    """ARQ entrypoint for the five-second download queue synchronisation."""
    redis = context["redis"]

    async def publish(event_type: str, data: dict[str, Any]) -> None:
        await publish_event(redis, event_type, data)

    async with session_scope() as session:

        async def enqueue_import(job_row: DownloadJob, output_path: str | None) -> None:
            await stage_completed_download(session, job_row, output_path, get_settings())

        async def failure_handler(job_row: DownloadJob) -> None:
            await handle_download_failure(session, redis, job_row)

        await poll_downloads(
            session,
            adapters=DOWNLOAD_CLIENT_ADAPTERS,
            publish=publish,
            enqueue_import=enqueue_import,
            handle_failure=failure_handler,
        )
    await dispatch_committed_triggers(redis)


DOWNLOAD_POLL_JOB = job(download_poll)
