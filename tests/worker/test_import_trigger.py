"""Durable completed-download import handover."""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from watchfiles import Change

from pornarr_db.base import Base
from pornarr_db.models.download import DownloadJob, ImportTrigger
from pornarr_db.types import set_cipher
from pornarr_shared.config import Settings
from pornarr_shared.crypto import CredentialCipher
from pornarr_shared.jobs import IMPORT_QUEUE, job_key
from pornarr_worker.jobs.import_intake import IntakeDecision, IntakeReason, IntakeResult
from pornarr_worker.jobs.import_trigger import (
    discover_download_files,
    dispatch_pending_triggers,
    process_import_trigger,
    stage_changed_download_files,
    stage_completed_download,
)

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


def settings(data_path: Path) -> Settings:
    return Settings(
        app_secret=SECRET,
        database_url="sqlite+aiosqlite://",
        redis_url="redis://localhost:6379/7",
        data_path=data_path,
    )


def completed_download(protocol: str = "torrent") -> DownloadJob:
    return DownloadJob(
        client_name="client",
        protocol=protocol,
        release_guid="release-guid",
        client_job_id="client-job",
        status="completed",
    )


async def test_completion_path_is_durable_and_uses_one_deterministic_import_key(
    session: AsyncSession, tmp_path: Path
) -> None:
    configured = settings(tmp_path / "data")
    download_path = configured.torrents_path / "release"
    download_path.mkdir(parents=True)
    item = completed_download()
    session.add(item)
    await session.flush()

    first = await stage_completed_download(session, item, str(download_path), configured)
    await session.flush()
    second = await stage_completed_download(session, item, str(download_path), configured)
    await session.commit()

    queued: list[str] = []

    async def enqueue(trigger: ImportTrigger) -> None:
        queued.append(str(trigger.id))

    assert first.id == second.id
    assert first.source_path == str(download_path.resolve())
    assert await dispatch_pending_triggers(session, enqueue) == 1
    assert queued == [str(first.id)]
    assert job_key("import_download", queued[0], queue=IMPORT_QUEUE) == job_key(
        "import_download", str(second.id), queue=IMPORT_QUEUE
    )
    assert len(list(await session.scalars(select(ImportTrigger)))) == 1


async def test_unmapped_client_path_is_retained_with_an_actionable_error(
    session: AsyncSession, tmp_path: Path
) -> None:
    item = completed_download()
    session.add(item)
    await session.flush()

    trigger = await stage_completed_download(
        session, item, "/client/downloads/release", settings(tmp_path)
    )

    assert trigger.status == "failed"
    assert trigger.error_code == "path_not_visible"
    assert trigger.error_detail is not None and "Mount the same directory" in trigger.error_detail


async def test_removed_download_is_not_imported_and_keeps_a_specific_error(
    session: AsyncSession, tmp_path: Path
) -> None:
    configured = settings(tmp_path / "data")
    missing = configured.usenet_path / "gone.mkv"
    item = completed_download("usenet")
    session.add(item)
    await session.flush()
    trigger = await stage_completed_download(session, item, str(missing), configured)
    await session.flush()

    assert await process_import_trigger(session, trigger.id) == "failed"
    assert trigger.error_code == "download_removed"
    assert (
        trigger.error_detail is not None
        and "Keep completed downloads available" in trigger.error_detail
    )


async def test_trigger_directory_creates_each_file_only_once_after_a_restart(
    session: AsyncSession, tmp_path: Path
) -> None:
    configured = settings(tmp_path / "data")
    download_path = configured.torrents_path / "release"
    download_path.mkdir(parents=True)
    media_file = download_path / "feature.mkv"
    media_file.write_bytes(b"media")
    item = completed_download()
    session.add(item)
    await session.flush()
    parent = await stage_completed_download(session, item, str(download_path), configured)
    await session.flush()

    assert await process_import_trigger(session, parent.id) == "discovered"
    await session.flush()
    assert await process_import_trigger(session, parent.id) == "discovered"

    triggers = list(
        await session.scalars(select(ImportTrigger).order_by(ImportTrigger.source_path))
    )
    assert [trigger.source_path for trigger in triggers] == [
        str(download_path.resolve()),
        str(media_file.resolve()),
    ]

    async def accepts(_: Path) -> IntakeResult:
        return IntakeResult(IntakeDecision.ACCEPT)

    file_trigger = triggers[1]
    assert await process_import_trigger(session, file_trigger.id, validate=accepts) == "ready"


async def test_processing_the_same_ready_file_twice_does_not_change_its_state(
    session: AsyncSession, tmp_path: Path
) -> None:
    source = tmp_path / "feature.mkv"
    source.write_bytes(b"media")
    trigger = ImportTrigger(source_path=str(source))
    session.add(trigger)
    await session.flush()

    async def accepts(_: Path) -> IntakeResult:
        return IntakeResult(IntakeDecision.ACCEPT)

    assert await process_import_trigger(session, trigger.id, validate=accepts) == "ready"
    await session.flush()
    await session.refresh(trigger)
    state_before_retry = (
        trigger.status,
        trigger.error_code,
        trigger.error_detail,
        trigger.updated_at,
    )

    assert await process_import_trigger(session, trigger.id, validate=accepts) == "ready"
    await session.flush()
    await session.refresh(trigger)

    assert (trigger.status, trigger.error_code, trigger.error_detail, trigger.updated_at) == (
        state_before_retry
    )
    assert list(await session.scalars(select(ImportTrigger))) == [trigger]
    assert source.read_bytes() == b"media"


async def test_intake_rejections_remain_visible_on_the_import_trigger(
    session: AsyncSession, tmp_path: Path
) -> None:
    source = tmp_path / "sample-feature.mkv"
    source.write_bytes(b"x" * (60 * 1024**2))
    trigger = ImportTrigger(source_path=str(source))
    session.add(trigger)
    await session.flush()

    assert await process_import_trigger(session, trigger.id) == "rejected"
    await session.commit()
    session.expunge_all()
    stored = await session.get(ImportTrigger, trigger.id)

    assert stored is not None and stored.status == "rejected"
    assert stored.error_code == IntakeReason.SAMPLE
    assert stored.error_detail == "Sample files are never imported as the main feature."


async def test_intake_retries_still_being_written_files(
    session: AsyncSession, tmp_path: Path
) -> None:
    source = tmp_path / "feature.mkv.part"
    source.write_bytes(b"partial")
    trigger = ImportTrigger(source_path=str(source))
    session.add(trigger)
    await session.flush()

    assert await process_import_trigger(session, trigger.id) == "retry"
    assert trigger.status == "pending"
    assert trigger.error_code == IntakeReason.WRITING
    assert (
        trigger.error_detail
        == "The source file is still being written and will be retried automatically."
    )


async def test_filesystem_watcher_fallback_discovers_manually_placed_media(
    session: AsyncSession, tmp_path: Path
) -> None:
    configured = settings(tmp_path / "data")
    manual = configured.usenet_path / "manual" / "feature.mp4"
    manual.parent.mkdir(parents=True)
    manual.write_bytes(b"media")

    assert await discover_download_files(session, configured) == 1
    assert await discover_download_files(session, configured) == 0
    trigger = await session.scalar(select(ImportTrigger))
    assert trigger is not None and trigger.source_path == str(manual.resolve())


async def test_native_watcher_events_only_stage_visible_media_files(
    session: AsyncSession, tmp_path: Path
) -> None:
    configured = settings(tmp_path / "data")
    manual = configured.torrents_path / "manual" / "feature.mkv"
    ignored = tmp_path / "outside.mp4"
    manual.parent.mkdir(parents=True)
    manual.write_bytes(b"media")
    ignored.write_bytes(b"media")

    assert (
        await stage_changed_download_files(
            session,
            {(Change.added, str(manual)), (Change.added, str(ignored))},
            configured,
        )
        == 1
    )
    trigger = await session.scalar(select(ImportTrigger))
    assert trigger is not None and trigger.source_path == str(manual.resolve())
