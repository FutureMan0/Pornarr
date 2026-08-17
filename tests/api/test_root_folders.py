"""Administrator root-folder configuration behaviour."""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from pornarr_db.models.media import Media, MediaFile
from pornarr_db.models.user import UserRole
from tests.api.test_auth import MemoryQueue, create_user, csrf_headers, login

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


async def test_scanning_a_folder_enqueues_one_walk_however_often_it_is_asked(
    app, client, tmp_path: Path
) -> None:
    """Two clicks mean one scan, not two racing over the same files."""
    app.state.settings = app.state.settings.model_copy(update={"data_path": tmp_path})
    app.state.settings.torrents_path.mkdir()
    app.state.settings.usenet_path.mkdir()
    root_folder = tmp_path / "library"
    root_folder.mkdir()
    admin = await create_user(app, role=UserRole.ADMIN)
    await login(client, admin.username, "correct horse battery staple")
    app.state.queue = MemoryQueue()

    created = await client.post(
        "/api/admin/library/root-folders",
        json={"path": str(root_folder)},
        headers=csrf_headers(client),
    )
    folder_id = created.json()["id"]

    first = await client.post(
        f"/api/admin/library/root-folders/{folder_id}/scan", headers=csrf_headers(client)
    )
    second = await client.post(
        f"/api/admin/library/root-folders/{folder_id}/scan", headers=csrf_headers(client)
    )

    assert first.status_code == 202
    assert second.status_code == 202
    function, args, kwargs = app.state.queue.jobs[0]
    assert function == "scan"
    assert args == (folder_id,)
    # The import worker is the one that runs `scan`. On the default queue the
    # job would wait forever with nobody to notice.
    assert kwargs["_queue_name"] == "pornarr:import"
    # Both calls carry the same job id, which is what makes the second a no-op
    # at the queue rather than a second walk.
    assert app.state.queue.jobs[1][2]["_job_id"] == kwargs["_job_id"]


async def test_a_disabled_folder_is_not_scanned_on_request(app, client, tmp_path: Path) -> None:
    app.state.settings = app.state.settings.model_copy(update={"data_path": tmp_path})
    app.state.settings.torrents_path.mkdir()
    app.state.settings.usenet_path.mkdir()
    root_folder = tmp_path / "library"
    root_folder.mkdir()
    admin = await create_user(app, role=UserRole.ADMIN)
    await login(client, admin.username, "correct horse battery staple")
    app.state.queue = MemoryQueue()

    created = await client.post(
        "/api/admin/library/root-folders",
        json={"path": str(root_folder), "enabled": False},
        headers=csrf_headers(client),
    )

    response = await client.post(
        f"/api/admin/library/root-folders/{created.json()['id']}/scan",
        headers=csrf_headers(client),
    )

    # Scanning it anyway would quietly reimport what somebody chose to exclude.
    assert response.status_code == 409
    assert app.state.queue.jobs == []


async def test_scanning_an_unknown_folder_is_a_404(app, client) -> None:
    admin = await create_user(app, role=UserRole.ADMIN)
    await login(client, admin.username, "correct horse battery staple")
    app.state.queue = MemoryQueue()

    response = await client.post(
        "/api/admin/library/root-folders/00000000-0000-0000-0000-000000000000/scan",
        headers=csrf_headers(client),
    )

    assert response.status_code == 404
