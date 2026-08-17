"""Administrator root-folder configuration behaviour."""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from pornarr_db.models.audit import AuditLog
from pornarr_db.models.media import Media, MediaFile
from pornarr_db.models.user import UserRole
from tests.api.test_auth import create_user, csrf_headers, login

pytest_plugins = ["tests.api.test_auth"]


async def test_admin_can_add_list_and_remove_a_writable_root_folder(
    app, client, tmp_path: Path
) -> None:
    app.state.settings = app.state.settings.model_copy(update={"data_path": tmp_path})
    app.state.settings.torrents_path.mkdir()
    app.state.settings.usenet_path.mkdir()
    root_folder = tmp_path / "library"
    root_folder.mkdir()
    admin = await create_user(app, role=UserRole.ADMIN)
    await login(client, admin.username, "correct horse battery staple")

    created = await client.post(
        "/api/admin/library/root-folders",
        json={"path": str(root_folder)},
        headers=csrf_headers(client),
    )

    assert created.status_code == 201
    folder = created.json()
    assert folder["path"] == str(root_folder.resolve())
    assert folder["enabled"] is True
    assert folder["free_space_bytes"] > 0
    assert folder["same_filesystem_as_downloads"] is True
    assert folder["warning"] is None
    assert (await client.get("/api/admin/library/root-folders")).json() == [folder]

    removed = await client.delete(
        f"/api/admin/library/root-folders/{folder['id']}", headers=csrf_headers(client)
    )

    assert removed.status_code == 204


async def test_root_folder_rejects_an_unwritable_path(
    app, client, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    app.state.settings = app.state.settings.model_copy(update={"data_path": tmp_path})
    app.state.settings.torrents_path.mkdir()
    root_folder = tmp_path / "library"
    root_folder.mkdir()
    admin = await create_user(app, role=UserRole.ADMIN)
    await login(client, admin.username, "correct horse battery staple")
    monkeypatch.setattr("pornarr_api.routers.admin_library.os.access", lambda *_: False)

    response = await client.post(
        "/api/admin/library/root-folders",
        json={"path": str(root_folder)},
        headers=csrf_headers(client),
    )

    assert response.status_code == 422
    assert response.json() == {
        "code": "ROOT_FOLDER_INVALID",
        "status": 422,
        "context": {"reason": "not_readable"},
    }


async def test_admin_can_trigger_a_scan_of_a_configured_root_folder(
    app, client, tmp_path: Path
) -> None:
    app.state.settings = app.state.settings.model_copy(update={"data_path": tmp_path})
    app.state.settings.torrents_path.mkdir()
    root_folder = tmp_path / "library"
    root_folder.mkdir()
    admin = await create_user(app, role=UserRole.ADMIN)
    await login(client, admin.username, "correct horse battery staple")
    created = await client.post(
        "/api/admin/library/root-folders",
        json={"path": str(root_folder)},
        headers=csrf_headers(client),
    )

    response = await client.post(
        f"/api/admin/library/root-folders/{created.json()['id']}/scan",
        headers=csrf_headers(client),
    )

    assert response.status_code == 202
    assert response.content == b""
    function, args, kwargs = app.state.job_queue.calls[0]
    folder_id, run_id = args
    assert function == "scan"
    assert folder_id == created.json()["id"]
    assert isinstance(run_id, str)
    assert run_id.startswith("manual:")
    assert kwargs["_queue_name"] == "pornarr:import"
    async with AsyncSession(app.state.engine) as session:
        entry = await session.scalar(
            select(AuditLog).where(AuditLog.action == "root_folder.scanned")
        )
    assert entry is not None
    assert entry.target == created.json()["id"]


async def test_scanning_an_unknown_or_disabled_root_folder_is_refused(
    app, client, tmp_path: Path
) -> None:
    app.state.settings = app.state.settings.model_copy(update={"data_path": tmp_path})
    app.state.settings.torrents_path.mkdir()
    root_folder = tmp_path / "library"
    root_folder.mkdir()
    admin = await create_user(app, role=UserRole.ADMIN)
    await login(client, admin.username, "correct horse battery staple")
    created = await client.post(
        "/api/admin/library/root-folders",
        json={"path": str(root_folder), "enabled": False},
        headers=csrf_headers(client),
    )

    missing = await client.post(
        f"/api/admin/library/root-folders/{uuid4()}/scan", headers=csrf_headers(client)
    )
    disabled = await client.post(
        f"/api/admin/library/root-folders/{created.json()['id']}/scan",
        headers=csrf_headers(client),
    )

    assert missing.status_code == 404
    assert disabled.status_code == 409
    assert disabled.json() == {"code": "ROOT_FOLDER_DISABLED", "status": 409, "context": {}}
    assert app.state.job_queue.calls == []


async def test_a_regular_user_cannot_trigger_a_scan(app, client, tmp_path: Path) -> None:
    app.state.settings = app.state.settings.model_copy(update={"data_path": tmp_path})
    app.state.settings.torrents_path.mkdir()
    user = await create_user(app)
    await login(client, user.username, "correct horse battery staple")

    response = await client.post(
        f"/api/admin/library/root-folders/{uuid4()}/scan", headers=csrf_headers(client)
    )

    assert response.status_code == 403
    assert app.state.job_queue.calls == []


async def test_root_folder_cannot_be_removed_while_it_contains_media(
    app, client, tmp_path: Path
) -> None:
    app.state.settings = app.state.settings.model_copy(update={"data_path": tmp_path})
    app.state.settings.torrents_path.mkdir()
    root_folder = tmp_path / "library"
    root_folder.mkdir()
    admin = await create_user(app, role=UserRole.ADMIN)
    await login(client, admin.username, "correct horse battery staple")
    created = await client.post(
        "/api/admin/library/root-folders",
        json={"path": str(root_folder)},
        headers=csrf_headers(client),
    )
    async with AsyncSession(app.state.engine) as session:
        media = Media(title="Example", normalized_title="example")
        session.add(media)
        await session.flush()
        session.add(MediaFile(media_id=media.id, path=str(root_folder / "example.mp4"), size=1))
        await session.commit()

    response = await client.delete(
        f"/api/admin/library/root-folders/{created.json()['id']}", headers=csrf_headers(client)
    )

    assert response.status_code == 409
    assert response.json() == {"code": "ROOT_FOLDER_HAS_MEDIA", "status": 409, "context": {}}
