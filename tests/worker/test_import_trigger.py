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
from pornarr_db.models.request import Request, RequestStatus
from pornarr_db.models.user import User
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


async def test_stage_completed_download_advances_its_requests_to_processing(
    session: AsyncSession, tmp_path: Path
) -> None:
    """`packages/db/pornarr_db/requests.py:29-34` declares `processing` a legal
    transition and nothing reached it before this handover did - "the import
    when it begins" is exactly what this function is.
    """
    configured = settings(tmp_path / "data")
    download_path = configured.torrents_path / "release"
    download_path.mkdir(parents=True)
    item = completed_download()
    session.add(item)
    await session.flush()
    user = User(username="requester", password_hash="not-a-real-password")
    session.add(user)
    await session.flush()
    request = Request(
        user_id=user.id,
        query="Example",
        status=RequestStatus.DOWNLOADING,
        priority=80,
        download_job_id=item.id,
    )
    session.add(request)
    await session.commit()

    await stage_completed_download(session, item, str(download_path), configured)
    await session.commit()

    assert request.status is RequestStatus.PROCESSING


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


@pytest.mark.parametrize(
    ("protocol", "reported_path"),
    [
        # The exact symptom docs/operations/troubleshooting.md L41-45 describes:
        # a client running outside the container reporting a host path.
        ("torrent", "/mnt/tank/media/downloads/complete/Release.mkv"),
        ("torrent", "C:/Downloads/Release.mkv"),
        ("usenet", "/home/operator/Downloads/Release.mkv"),
        # Inside the data mount, but under the other protocol's tree.
        ("torrent", "/data/usenet/complete/Release.mkv"),
        ("usenet", "/data/torrents/completed/Release.mkv"),
        # Inside the data mount but outside both download trees.
        ("torrent", "/data/library/Studio/Release.mkv"),
        # A traversal that resolves back out of the tree.
        ("torrent", "/data/torrents/../../etc/Release.mkv"),
        # Relative, which no absolute mount can make visible.
        ("torrent", "downloads/Release.mkv"),
    ],
)
async def test_a_path_the_worker_cannot_resolve_is_named_rather_than_imported(
    session: AsyncSession, tmp_path: Path, protocol: str, reported_path: str
) -> None:
    """Downloads complete but nothing is imported, with the reason on the trigger."""
    configured = settings(tmp_path / "data")
    item = completed_download(protocol)
    session.add(item)
    await session.flush()

    trigger = await stage_completed_download(session, item, reported_path, configured)

    assert trigger.status == "failed"
    assert trigger.error_code == "path_not_visible"
    assert trigger.error_detail is not None
    # Actionable means naming the path that was reported and the root it has to
    # be mounted at - a bare "not visible" leaves the operator guessing.
    assert reported_path in trigger.error_detail
    if reported_path.startswith("/"):
        expected_root = str(
            (
                configured.torrents_path if protocol == "torrent" else configured.usenet_path
            ).resolve()
        )
        assert expected_root in trigger.error_detail
    # Nothing was handed on: a failed trigger is not dispatchable work.
    assert trigger.reported_path == reported_path


@pytest.mark.parametrize(
    ("protocol", "reported_path"),
    [
        ("torrent", "torrents/completed/Release.mkv"),
        ("torrent", "torrents/completed/nested/dir/Release.mkv"),
        ("usenet", "usenet/complete/Release.mkv"),
    ],
)
async def test_a_path_below_the_shared_mount_is_translated_rather_than_refused(
    session: AsyncSession, tmp_path: Path, protocol: str, reported_path: str
) -> None:
    """The negative space: the same check must not fire on a correct mount."""
    configured = settings(tmp_path / "data")
    absolute = configured.data_path / reported_path
    item = completed_download(protocol)
    session.add(item)
    await session.flush()

    trigger = await stage_completed_download(session, item, str(absolute), configured)

    assert trigger.status == "pending"
    assert trigger.error_code is None
    assert trigger.source_path == str(absolute.resolve())
