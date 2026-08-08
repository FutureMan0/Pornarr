"""First-run setup locks the instance until an admin is created."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from sqlalchemy import func, select

from pornarr_db.models.filters import ContentFilterProfile, ContentFilterRule, FilterProfileScope
from pornarr_db.models.root_folders import RootFolder
from pornarr_db.models.user import User
from tests.api.test_auth import login

pytest_plugins = ("tests.api.test_auth",)


async def test_fresh_instance_serves_only_setup_then_unlocks(app, client, tmp_path: Path) -> None:
    app.state.settings = app.state.settings.model_copy(update={"data_path": tmp_path})
    app.state.settings.torrents_path.mkdir()
    library_path = tmp_path / "library"
    library_path.mkdir()

    assert (await client.get("/api/auth/me")).status_code == 503
    assert (
        await client.post("/api/auth/login", json={"username": "admin", "password": "x"})
    ).status_code == 503
    completed = await client.post(
        "/api/setup/complete",
        json={
            "username": "admin",
            "password": "correct horse battery staple",
            "library_path": str(library_path),
        },
    )

    assert completed.status_code == 201
    assert completed.json()["same_filesystem_as_downloads"] is True
    assert completed.json()["warning"] is None
    async with app.state.engine.connect() as connection:
        assert await connection.scalar(select(User.id)) is not None
        assert await connection.scalar(select(RootFolder.id)) is not None
        profile_id = await connection.scalar(
            select(ContentFilterProfile.id).where(
                ContentFilterProfile.scope == FilterProfileScope.GLOBAL
            )
        )
        assert profile_id is not None
        rule_count = await connection.scalar(
            select(func.count(ContentFilterRule.id)).where(
                ContentFilterRule.profile_id == profile_id
            )
        )
        assert rule_count == 6
    repeated = await client.post(
        "/api/setup/complete",
        json={
            "username": "second-admin",
            "password": "correct horse battery staple",
            "library_path": str(library_path),
        },
    )
    assert repeated.status_code == 409
    assert repeated.json()["code"] == "SETUP_ALREADY_COMPLETED"
    await login(client, "admin", "correct horse battery staple")
    assert (await client.get("/api/auth/me")).status_code == 200


async def test_setup_reports_a_different_filesystem(
    app, client, tmp_path: Path, monkeypatch
) -> None:
    app.state.settings = app.state.settings.model_copy(update={"data_path": tmp_path})
    torrents_path = tmp_path / "torrents"
    library_path = tmp_path / "library"
    torrents_path.mkdir()
    library_path.mkdir()
    original_stat = Path.stat

    def stat_on_other_device(path: Path, *args, **kwargs):
        result = original_stat(path, *args, **kwargs)
        if path == library_path:
            return SimpleNamespace(st_dev=result.st_dev + 1, st_mode=result.st_mode)
        return result

    monkeypatch.setattr(Path, "stat", stat_on_other_device)

    response = await client.post(
        "/api/setup/complete",
        json={
            "username": "admin",
            "password": "correct horse battery staple",
            "library_path": str(library_path),
        },
    )

    assert response.status_code == 201
    assert response.json()["same_filesystem_as_downloads"] is False
    assert (
        response.json()["warning"] == "different filesystem from downloads; imports cannot hardlink"
    )
