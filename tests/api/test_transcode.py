"""Administrator visibility and control of transcode sessions."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from pornarr_db.models.user import UserRole
from pornarr_media.transcode import HlsPaths
from tests.api.test_auth import create_user, csrf_headers, login

pytest_plugins = ["tests.api.test_auth"]


@dataclass
class FakeProcess:
    pid: int = 1


class FakeTranscode:
    def __init__(self, directory: Path) -> None:
        self.paths = HlsPaths(
            directory=directory,
            master_playlist=directory / "master.m3u8",
            variant_playlist=directory / "variant.m3u8",
            segment_pattern=directory / "segment_%05d.ts",
        )
        self.process = FakeProcess()
        self.stopped = False

    async def stop(self) -> None:
        self.stopped = True


async def test_admin_can_list_and_terminate_a_transcode_session(
    app, client, tmp_path: Path
) -> None:
    from pornarr_media.sessions import TranscodeSessionRegistry

    app.state.settings = app.state.settings.model_copy(update={"data_path": tmp_path})
    registry = TranscodeSessionRegistry(app.state.redis, app.state.settings.transcode_path)
    app.state.transcode_sessions = registry
    user = await create_user(app)
    admin = await create_user(app, username="admin", role=UserRole.ADMIN)
    session_id = uuid4()
    directory = app.state.settings.transcode_path / str(session_id)
    directory.mkdir(parents=True)
    transcode = FakeTranscode(directory)
    await registry.register(session_id, user.id, uuid4(), "hls", transcode)
    await login(client, admin.username, "correct horse battery staple")

    sessions = await client.get("/api/admin/transcode/sessions")

    assert sessions.status_code == 200
    assert sessions.json()[0]["id"] == str(session_id)
    assert sessions.json()[0]["user_id"] == str(user.id)
    assert sessions.json()[0]["mode"] == "hls"

    terminated = await client.delete(
        f"/api/admin/transcode/sessions/{session_id}", headers=csrf_headers(client)
    )

    assert terminated.status_code == 204
    assert transcode.stopped is True


async def test_only_the_session_owner_can_heartbeat(app, client, tmp_path: Path) -> None:
    from pornarr_media.sessions import TranscodeSessionRegistry

    app.state.settings = app.state.settings.model_copy(update={"data_path": tmp_path})
    registry = TranscodeSessionRegistry(app.state.redis, app.state.settings.transcode_path)
    app.state.transcode_sessions = registry
    owner = await create_user(app)
    other = await create_user(app, username="other")
    session_id = uuid4()
    directory = app.state.settings.transcode_path / str(session_id)
    directory.mkdir(parents=True)
    await registry.register(session_id, owner.id, uuid4(), "hls", FakeTranscode(directory))
    await login(client, other.username, "correct horse battery staple")

    heartbeat = await client.post(
        f"/api/transcode/sessions/{session_id}/heartbeat", headers=csrf_headers(client)
    )

    assert heartbeat.status_code == 403
