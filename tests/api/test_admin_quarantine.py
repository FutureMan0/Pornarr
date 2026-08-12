"""Administrator quarantine review API behaviour."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from pornarr_db.models.audit import AuditLog
from pornarr_db.models.media import Media, MediaFile
from pornarr_db.models.quarantine import QuarantineItem
from pornarr_db.models.user import UserRole
from pornarr_worker.jobs.quarantine import (
    QuarantineReason,
    QuarantineReasonCode,
    quarantine_file,
)
from tests.api.test_auth import create_user, csrf_headers, login

pytest_plugins = ("tests.api.test_auth",)


async def test_admin_can_list_and_approve_a_quarantined_item_with_corrections(
    app, client, tmp_path: Path
) -> None:
    item = await _quarantine_item(app, tmp_path, "ambiguous.mkv")
    admin = await create_user(app, role=UserRole.ADMIN)
    await login(client, admin.username, "correct horse battery staple")

    listed = await client.get("/api/admin/quarantine")

    assert listed.status_code == 200
    assert listed.json() == [
        {
            "id": str(item.id),
            "original_path": "/downloads/ambiguous.mkv",
            "quarantine_path": item.quarantine_path,
            "reasons": item.reasons,
            "extracted_metadata": item.extracted_metadata,
            "technical_details": item.technical_details,
            "created_at": item.created_at.isoformat().replace("+00:00", "Z"),
        }
    ]

    approved = await client.post(
        f"/api/admin/quarantine/items/{item.id}/approve",
        json={
            "title": "Correct Scene",
            "studio": "Correct Studio",
            "release_date": "2024-01-02",
            "quality": "1080p",
        },
        headers=csrf_headers(client),
    )

    assert approved.status_code == 200
    response = approved.json()
    assert response["placement_method"] == "hardlink"
    assert Path(response["path"]).is_relative_to(tmp_path / "library")
    async with AsyncSession(app.state.engine) as session:
        media = await session.get(Media, UUID(response["media_id"]))
        assert media is not None
        media_file = await session.scalar(select(MediaFile).where(MediaFile.media_id == media.id))
        audit = await session.scalar(
            select(AuditLog).where(AuditLog.action == "quarantine.approved")
        )
        assert media.release_date is not None
        assert (media.title, media.studio, media.release_date.isoformat()) == (
            "Correct Scene",
            "Correct Studio",
            "2024-01-02",
        )
        assert media_file is not None
        assert media_file.quality == "1080p"
        assert (media_file.container, media_file.resolution) == ("matroska", "1080p")
        assert await session.get(QuarantineItem, item.id) is None
        assert audit is not None
        assert audit.actor_id == admin.id
    assert not Path(item.quarantine_path).exists()

    source = tmp_path / "next-ambiguous.mkv"
    source.write_bytes(b"media")
    async with AsyncSession(app.state.engine, expire_on_commit=False) as session:
        next_item = await quarantine_file(
            session,
            source,
            app.state.settings.quarantine_path,
            _reasons(),
            extracted_metadata={"title": "Ambiguous Scene", "confidence": 0.4},
        )
        await session.commit()

    assert next_item.extracted_metadata["title"] == "Correct Scene"
    assert next_item.extracted_metadata["studio"] == "Correct Studio"


async def test_admin_rejection_deletes_the_file_and_audits_the_decision(
    app, client, tmp_path: Path
) -> None:
    item = await _quarantine_item(app, tmp_path, "reject.mkv")
    admin = await create_user(app, role=UserRole.ADMIN)
    await login(client, admin.username, "correct horse battery staple")

    rejected = await client.post(
        f"/api/admin/quarantine/items/{item.id}/reject", headers=csrf_headers(client)
    )

    assert rejected.status_code == 204
    assert not Path(item.quarantine_path).exists()
    async with AsyncSession(app.state.engine) as session:
        assert await session.get(QuarantineItem, item.id) is None
        audit = await session.scalar(
            select(AuditLog).where(AuditLog.action == "quarantine.rejected")
        )
        assert audit is not None
        assert audit.actor_id == admin.id


async def test_bulk_approval_requires_a_preview_and_only_accepts_a_shared_reason(
    app, client, tmp_path: Path
) -> None:
    first = await _quarantine_item(app, tmp_path, "first.mkv")
    second = await _quarantine_item(app, tmp_path, "second.mkv")
    admin = await create_user(app, role=UserRole.ADMIN)
    await login(client, admin.username, "correct horse battery staple")
    payload = {
        "item_ids": [str(first.id), str(second.id)],
        "reason_code": "low_confidence",
    }

    direct = await client.post(
        "/api/admin/quarantine/bulk/approve",
        json={**payload, "token": "0" * 64},
        headers=csrf_headers(client),
    )
    preview = await client.post(
        "/api/admin/quarantine/bulk/preview", json=payload, headers=csrf_headers(client)
    )

    assert direct.status_code == 422
    assert preview.status_code == 200
    assert [entry["id"] for entry in preview.json()["items"]] == [str(first.id), str(second.id)]

    async with AsyncSession(app.state.engine) as session:
        updated = await session.get(QuarantineItem, first.id)
        assert updated is not None
        updated.technical_details = {"container": "matroska", "resolution": "2160p"}
        updated.updated_at = datetime.now(UTC)
        await session.commit()

    stale = await client.post(
        "/api/admin/quarantine/bulk/approve",
        json={**payload, "token": preview.json()["token"]},
        headers=csrf_headers(client),
    )
    refreshed_preview = await client.post(
        "/api/admin/quarantine/bulk/preview", json=payload, headers=csrf_headers(client)
    )
    approved = await client.post(
        "/api/admin/quarantine/bulk/approve",
        json={**payload, "token": refreshed_preview.json()["token"]},
        headers=csrf_headers(client),
    )

    assert stale.status_code == 422
    assert approved.status_code == 200
    assert len(approved.json()["approved"]) == 2
    async with AsyncSession(app.state.engine) as session:
        assert list(await session.scalars(select(QuarantineItem))) == []
        assert len(list(await session.scalars(select(Media)))) == 2
        audit = await session.scalar(
            select(AuditLog).where(AuditLog.action == "quarantine.bulk_approved")
        )
        assert audit is not None


async def _quarantine_item(app, tmp_path: Path, filename: str) -> QuarantineItem:
    app.state.settings = app.state.settings.model_copy(update={"data_path": tmp_path})
    quarantine_root = app.state.settings.quarantine_path
    source = tmp_path / filename
    source.write_bytes(b"media")
    destination = quarantine_root / filename
    destination.parent.mkdir(parents=True, exist_ok=True)
    source.replace(destination)
    async with AsyncSession(app.state.engine, expire_on_commit=False) as session:
        item = QuarantineItem(
            original_path=f"/downloads/{filename}",
            quarantine_path=str(destination),
            reasons=[reason.as_payload() for reason in _reasons()],
            extracted_metadata={"title": "Ambiguous Scene", "confidence": 0.4},
            technical_details={"container": "matroska", "resolution": "1080p"},
        )
        session.add(item)
        await session.commit()
        return item


def _reasons() -> list[QuarantineReason]:
    return [
        QuarantineReason(
            QuarantineReasonCode.LOW_CONFIDENCE,
            "metadata confidence 0.40 is below the required 0.80",
            {"actual": 0.4, "minimum": 0.8},
        ),
        QuarantineReason(
            QuarantineReasonCode.FILTER_RULE,
            "filter rule example matched tag 'example'",
            {"rule_id": "example", "pattern": "example"},
        ),
    ]
