"""Quarantine keeps uncertain files out of the library and reviewable."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from pornarr_db.base import Base
from pornarr_db.models.quarantine import QuarantineItem
from pornarr_worker.jobs.quarantine import (
    QuarantineReason,
    QuarantineReasonCode,
    prune_quarantine,
    quarantine_file,
)


@pytest.fixture
async def session():
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    try:
        async with AsyncSession(engine, expire_on_commit=False) as database_session:
            yield database_session
    finally:
        await engine.dispose()


async def test_quarantine_moves_the_file_and_preserves_every_actionable_reason(
    session: AsyncSession, tmp_path: Path
) -> None:
    source = tmp_path / "downloads" / "Release?.mkv"
    source.parent.mkdir()
    source.write_bytes(b"media")
    library = tmp_path / "library"
    reasons = [
        QuarantineReason(
            QuarantineReasonCode.LOW_CONFIDENCE,
            "metadata confidence 0.42 is below the required 0.80",
            {"actual": 0.42, "minimum": 0.80},
        ),
        QuarantineReason(
            QuarantineReasonCode.FILTER_RULE,
            "filter rule no-unknown-age matched tag 'unknown age'",
            {"rule_id": "no-unknown-age", "pattern": "unknown age"},
        ),
    ]

    item = await quarantine_file(session, source, tmp_path / "quarantine", reasons)

    destination = Path(item.quarantine_path)
    assert not source.exists()
    assert destination.read_bytes() == b"media"
    assert destination.is_relative_to((tmp_path / "quarantine").resolve())
    assert not destination.is_relative_to(library.resolve())
    assert item.reasons == [reason.as_payload() for reason in reasons]
    assert list(await session.scalars(select(QuarantineItem))) == [item]


async def test_quarantine_retention_removes_only_expired_files_and_records(
    session: AsyncSession, tmp_path: Path
) -> None:
    root = tmp_path / "quarantine"
    expired_source = tmp_path / "expired.mkv"
    retained_source = tmp_path / "retained.mkv"
    expired_source.write_bytes(b"expired")
    retained_source.write_bytes(b"retained")
    reason = QuarantineReason(
        QuarantineReasonCode.UNEXPECTED_FILE_TYPE,
        "extension .exe is not an accepted media type",
        {"extension": ".exe"},
    )
    expired = await quarantine_file(session, expired_source, root, [reason])
    retained = await quarantine_file(session, retained_source, root, [reason])
    expired.created_at = datetime.now(UTC) - timedelta(days=31)
    await session.flush()

    deleted = await prune_quarantine(session, root, retention_days=30)
    await session.flush()

    assert deleted == 1
    assert not Path(expired.quarantine_path).exists()
    assert Path(retained.quarantine_path).exists()
    assert list(await session.scalars(select(QuarantineItem))) == [retained]


async def test_quarantine_retention_never_deletes_a_path_outside_the_quarantine_root(
    session: AsyncSession, tmp_path: Path
) -> None:
    outside = tmp_path / "outside.mkv"
    outside.write_bytes(b"keep")
    item = QuarantineItem(
        original_path="/downloads/outside.mkv",
        quarantine_path=str(outside),
        reasons=[
            {"code": "contradictory_duplicate", "detail": "conflicting results", "evidence": {}}
        ],
    )
    session.add(item)
    await session.flush()
    item.created_at = datetime.now(UTC) - timedelta(days=31)
    await session.flush()

    deleted = await prune_quarantine(session, tmp_path / "quarantine", retention_days=30)

    assert deleted == 0
    assert outside.read_bytes() == b"keep"
    assert await session.get(QuarantineItem, item.id) is item


async def test_quarantine_requires_a_specific_reason(session: AsyncSession, tmp_path: Path) -> None:
    source = tmp_path / "source.mkv"
    source.write_bytes(b"media")

    with pytest.raises(ValueError, match="at least one"):
        await quarantine_file(session, source, tmp_path / "quarantine", [])

    assert source.exists()


async def test_failed_quarantine_copy_leaves_no_partial_file(
    session: AsyncSession, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "downloads" / "source.mkv"
    source.parent.mkdir()
    source.write_bytes(b"media")
    quarantine_root = tmp_path / "quarantine"

    def interrupted_copy(_: Path, destination: Path, *args, **kwargs) -> None:
        destination.write_bytes(b"partial")
        raise OSError("copy interrupted")

    monkeypatch.setattr("pornarr_worker.jobs.quarantine.shutil.copy2", interrupted_copy)

    with pytest.raises(OSError, match="copy interrupted"):
        await quarantine_file(
            session=session,
            source=source,
            quarantine_root=quarantine_root,
            reasons=[
                QuarantineReason(
                    QuarantineReasonCode.FILTER_RULE,
                    "filter rule rejects this fixture",
                    {"rule_id": "fixture"},
                )
            ],
        )

    assert source.read_bytes() == b"media"
    assert not list(quarantine_root.rglob("*"))
