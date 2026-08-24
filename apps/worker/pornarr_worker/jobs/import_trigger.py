"""Durable handover from completed downloads to the import pipeline."""

from __future__ import annotations

import asyncio
import os
import stat
from collections.abc import Awaitable, Callable, Iterator
from pathlib import Path
from typing import Any
from uuid import UUID

from arq.worker import Retry
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from watchfiles import Change, awatch

from pornarr_db.models.download import DownloadJob, ImportTrigger
from pornarr_db.models.request import RequestStatus
from pornarr_db.requests import advance_requests_for_download
from pornarr_db.session import session_scope
from pornarr_shared.config import Settings, get_settings
from pornarr_shared.jobs import IMPORT_QUEUE, enqueue_once, job, retry_delay_seconds
from pornarr_worker.jobs.import_intake import (
    IntakeDecision,
    IntakeReason,
    IntakeResult,
    validate_import_file,
)

IMPORT_DOWNLOAD_JOB_NAME = "import_download"
IMPORT_MEDIA_JOB_NAME = "import_media"
PENDING = "pending"
READY = "ready"
WATCH_DURATION_SECONDS = 25

TriggerEnqueuer = Callable[[ImportTrigger], Awaitable[None]]
IntakeValidator = Callable[[Path], Awaitable[IntakeResult]]


class PathTranslationError(ValueError):
    """A download-client output path is not visible to the import worker."""


def translate_client_path(reported_path: str, protocol: str, settings: Settings) -> Path:
    """Return the worker-visible path, refusing mounts outside the shared data tree."""
    candidate = Path(reported_path)
    if not candidate.is_absolute():
        raise PathTranslationError(
            f"The download client reported relative path {reported_path!r}. "
            "Configure its completed-download path as an absolute path under the shared data mount."
        )
    root = _download_root(protocol, settings).resolve()
    resolved = candidate.resolve(strict=False)
    try:
        resolved.relative_to(root)
    except ValueError as error:
        raise PathTranslationError(
            f"The download client reported {reported_path!r}, but the worker can only import "
            f"{protocol} downloads below {str(root)!r}. Mount the same directory at that path "
            "for both the client and Pornarr."
        ) from error
    return resolved


async def stage_completed_download(
    session: AsyncSession,
    download_job: DownloadJob,
    reported_path: str | None,
    settings: Settings,
) -> ImportTrigger:
    """Persist exactly one client-completion trigger before anything reaches Redis."""
    # The request's own lifecycle calls this "the import beginning" whether it
    # ends up staged, deduplicated onto an existing trigger, or immediately
    # failed on a path the worker cannot see - an attempt was made either way,
    # and `download_poll` guarantees the request already passed through
    # `downloading` before this callback ever runs.
    await advance_requests_for_download(session, download_job.id, RequestStatus.PROCESSING)
    existing = await session.scalar(
        select(ImportTrigger).where(ImportTrigger.download_job_id == download_job.id)
    )
    if existing is not None:
        return existing

    if not reported_path:
        trigger = _failed_trigger(
            download_job,
            source_path=f"download-job:{download_job.id}",
            reported_path=None,
            code="output_path_missing",
            detail=(
                "The download client marked this job complete without reporting an output path. "
                "Upgrade or configure the client so completed downloads expose their final path."
            ),
            session=session,
        )
        await session.flush()
        return trigger
    try:
        source_path = translate_client_path(reported_path, download_job.protocol, settings)
    except PathTranslationError as error:
        trigger = _failed_trigger(
            download_job,
            source_path=reported_path,
            reported_path=reported_path,
            code="path_not_visible",
            detail=str(error),
            session=session,
        )
        await session.flush()
        return trigger

    existing_source = await session.scalar(
        select(ImportTrigger).where(ImportTrigger.source_path == str(source_path))
    )
    if existing_source is not None:
        existing_source.download_job_id = download_job.id
        await session.flush()
        return existing_source

    trigger = ImportTrigger(
        download_job_id=download_job.id,
        source_path=str(source_path),
        reported_path=reported_path,
    )
    session.add(trigger)
    await session.flush()
    return trigger


async def discover_download_files(session: AsyncSession, settings: Settings) -> int:
    """Reconcile the download trees so manually placed files enter the same pipeline."""
    discovered = 0
    for root in (_download_root("torrent", settings), _download_root("usenet", settings)):
        for path in _iter_candidate_files(root):
            if await _stage_watched_file(session, path):
                discovered += 1
    return discovered


async def dispatch_triggers(session: AsyncSession, status: str, enqueue: TriggerEnqueuer) -> int:
    """Put every durable trigger in one status back on the import queue."""
    triggers = list(
        await session.scalars(
            select(ImportTrigger)
            .where(ImportTrigger.status == status)
            .order_by(ImportTrigger.created_at, ImportTrigger.id)
        )
    )
    for trigger in triggers:
        await enqueue(trigger)
    return len(triggers)


async def dispatch_pending_triggers(session: AsyncSession, enqueue: TriggerEnqueuer) -> int:
    """Put every durable pending trigger back on the import queue after a restart."""
    return await dispatch_triggers(session, PENDING, enqueue)


async def process_import_trigger(
    session: AsyncSession, trigger_id: UUID, *, validate: IntakeValidator = validate_import_file
) -> str:
    """Resolve one trigger into file-level import work without duplicating it."""
    trigger = await session.get(ImportTrigger, trigger_id)
    if trigger is None:
        return "missing_trigger"
    if trigger.status != PENDING:
        return trigger.status

    source = Path(trigger.source_path)
    if not source.exists():
        _mark_failed(
            trigger,
            "download_removed",
            f"The completed download at {str(source)!r} was removed before import started. "
            "Keep completed downloads available until Pornarr has imported them.",
        )
        return trigger.status
    if source.is_dir():
        for path in _iter_candidate_files(source):
            await _stage_watched_file(session, path)
        trigger.status = "discovered"
        return trigger.status
    if not source.is_file() or source.is_symlink():
        _mark_failed(
            trigger, "unsupported_source", f"The import source {str(source)!r} is not a file."
        )
        return trigger.status
    intake = await validate(source)
    if intake.decision is IntakeDecision.RETRY:
        _mark_retry(trigger, intake.reason)
        return "retry"
    if intake.decision is IntakeDecision.REJECT:
        _mark_rejected(trigger, intake.reason)
        return trigger.status
    trigger.status = "ready"
    trigger.error_code = None
    trigger.error_detail = None
    return trigger.status


async def import_download(context: dict[str, Any], trigger_id: str) -> str:
    """ARQ entrypoint: resolve a committed trigger, then dispatch its file children."""
    async with session_scope() as session:
        result = await process_import_trigger(session, UUID(trigger_id))
    if result == "retry":
        raise Retry(defer=retry_delay_seconds(int(context["job_try"])))
    await dispatch_committed_triggers(context["redis"])
    return result


async def watch_download_files(context: dict[str, Any]) -> int:
    """Watch download roots briefly, with a reconciliation sweep for missed events."""
    settings = get_settings()
    async with session_scope() as session:
        discovered = await discover_download_files(session, settings)
    roots = [
        root
        for root in (_download_root("torrent", settings), _download_root("usenet", settings))
        if root.is_dir()
    ]
    if roots:
        stop_event = asyncio.Event()
        timeout = asyncio.get_running_loop().call_later(WATCH_DURATION_SECONDS, stop_event.set)
        try:
            async for changes in awatch(
                *roots,
                stop_event=stop_event,
                debounce=250,
                yield_on_timeout=True,
                ignore_permission_denied=True,
            ):
                if not changes:
                    continue
                async with session_scope() as session:
                    discovered += await stage_changed_download_files(session, changes, settings)
        finally:
            timeout.cancel()
    await dispatch_committed_triggers(context["redis"])
    return discovered


async def stage_changed_download_files(
    session: AsyncSession, changes: set[tuple[Change, str]], settings: Settings
) -> int:
    """Stage supported files from a native filesystem watch event batch."""
    roots = tuple(
        _download_root(protocol, settings).resolve() for protocol in ("torrent", "usenet")
    )
    discovered = 0
    for _, changed_path in changes:
        path = Path(changed_path)
        try:
            resolved = path.resolve(strict=False)
            if not any(_is_within(resolved, root) for root in roots):
                continue
            if not resolved.is_file() or resolved.is_symlink():
                continue
        except OSError:
            continue
        if await _stage_watched_file(session, resolved):
            discovered += 1
    return discovered


async def dispatch_committed_triggers(redis: Any) -> int:
    """Hand pending triggers to intake and validated ones to the importer.

    Both statuses are dispatched here rather than only at the transition that
    produced them, so a trigger that was left behind by a restart between the
    two steps is picked up on the next sweep instead of sitting in the table
    forever.
    """
    async with session_scope() as session:
        dispatched = 0
        for status, function in (
            (PENDING, IMPORT_DOWNLOAD_JOB_NAME),
            (READY, IMPORT_MEDIA_JOB_NAME),
        ):
            dispatched += await dispatch_triggers(
                session,
                status,
                _queue_trigger(redis, function),
            )
        return dispatched


def _queue_trigger(redis: Any, function: str) -> TriggerEnqueuer:
    async def enqueue(trigger: ImportTrigger) -> None:
        await enqueue_once(redis, function, str(trigger.id), queue=IMPORT_QUEUE)

    return enqueue


def _download_root(protocol: str, settings: Settings) -> Path:
    if protocol == "torrent":
        return settings.torrents_path
    if protocol == "usenet":
        return settings.usenet_path
    raise PathTranslationError(
        f"The completed download uses unsupported protocol {protocol!r}; expected 'torrent' or 'usenet'."
    )


def _iter_candidate_files(root: Path) -> Iterator[Path]:
    if not root.is_dir():
        return
    for directory, subdirectories, names in os.walk(root):
        subdirectories.sort()
        for name in sorted(names):
            path = Path(directory, name)
            if path.is_symlink():
                continue
            try:
                file_stat = path.stat()
            except OSError:
                continue
            if stat.S_ISREG(file_stat.st_mode):
                yield path.resolve()


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


async def _stage_watched_file(session: AsyncSession, path: Path) -> bool:
    existing = await session.scalar(
        select(ImportTrigger.id).where(ImportTrigger.source_path == str(path))
    )
    if existing is not None:
        return False
    session.add(ImportTrigger(source_path=str(path)))
    await session.flush()
    return True


def _failed_trigger(
    download_job: DownloadJob,
    *,
    source_path: str,
    reported_path: str | None,
    code: str,
    detail: str,
    session: AsyncSession,
) -> ImportTrigger:
    trigger = ImportTrigger(
        download_job_id=download_job.id,
        source_path=source_path,
        reported_path=reported_path,
        status="failed",
        error_code=code,
        error_detail=detail,
    )
    session.add(trigger)
    return trigger


def _mark_failed(trigger: ImportTrigger, code: str, detail: str) -> None:
    trigger.status = "failed"
    trigger.error_code = code
    trigger.error_detail = detail


def _mark_retry(trigger: ImportTrigger, reason: IntakeReason | None) -> None:
    trigger.error_code = (reason or IntakeReason.WRITING).value
    trigger.error_detail = (
        "The source file is still being written and will be retried automatically."
    )


def _mark_rejected(trigger: ImportTrigger, reason: IntakeReason | None) -> None:
    resolved_reason = reason or IntakeReason.UNSUPPORTED_EXTENSION
    trigger.status = "rejected"
    trigger.error_code = resolved_reason.value
    trigger.error_detail = _intake_rejection_detail(resolved_reason)


def _intake_rejection_detail(reason: IntakeReason) -> str:
    return {
        IntakeReason.ARCHIVE: "Archive files must be extracted before import.",
        IntakeReason.EMPTY: "The file is empty and cannot be imported.",
        IntakeReason.EXTRA: "The file is marked as extra content, not the main feature.",
        IntakeReason.MALWARE: "The optional malware scan rejected this file.",
        IntakeReason.SAMPLE: "Sample files are never imported as the main feature.",
        IntakeReason.TOO_SMALL: "The file is below the minimum import size.",
        IntakeReason.UNSUPPORTED_EXTENSION: "This file type is not supported for import.",
        IntakeReason.WRITING: "The file is still being written and cannot be imported yet.",
    }[reason]


IMPORT_DOWNLOAD_JOB = job(import_download)
WATCH_DOWNLOAD_FILES_JOB = job(watch_download_files)
