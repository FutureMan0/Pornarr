"""Administrator visibility and control of transcode sessions."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from pornarr_db.models.media import Media
from pornarr_db.models.user import UserRole
from pornarr_media.capabilities import (
    CodecCapability,
    HardwareAcceleration,
    HardwareCapabilities,
    HardwareCapability,
    HardwareRejection,
    VideoCodec,
)
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
    async with AsyncSession(app.state.engine, expire_on_commit=False) as database_session:
        media = Media(title="Example", normalized_title="example")
        database_session.add(media)
        await database_session.commit()
    session_id = uuid4()
    directory = app.state.settings.transcode_path / str(session_id)
    directory.mkdir(parents=True)
    transcode = FakeTranscode(directory)
    await registry.register(session_id, user.id, media.id, "hls", transcode, hardware=True)
    await login(client, admin.username, "correct horse battery staple")

    sessions = await client.get("/api/admin/transcode/sessions")

    assert sessions.status_code == 200
    assert sessions.json()[0]["id"] == str(session_id)
    assert sessions.json()[0]["user_id"] == str(user.id)
    assert sessions.json()[0]["username"] == user.username
    assert sessions.json()[0]["media_id"] == str(media.id)
    assert sessions.json()[0]["media_title"] == media.title
    assert sessions.json()[0]["mode"] == "hls"
    assert sessions.json()[0]["hardware"] is True
    assert sessions.json()[0]["elapsed_seconds"] >= 0

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


async def test_admin_can_see_current_transcode_limits(app, client) -> None:
    admin = await create_user(app, username="admin", role=UserRole.ADMIN)
    app.state.settings = app.state.settings.model_copy(
        update={
            "transcode_max_hw_sessions": 3,
            "transcode_max_sw_sessions": 4,
            "transcode_max_per_user": 2,
        }
    )
    app.state.hardware_capabilities = HardwareCapabilities(
        methods=(), rejections=(), nvidia_gpus=()
    )
    await login(client, admin.username, "correct horse battery staple")

    limits = await client.get("/api/admin/transcode/limits")

    assert limits.status_code == 200
    assert limits.json() == {
        "hardware": 3,
        "software": 4,
        "per_user": 2,
        "hardware_in_use": 0,
        "software_in_use": 0,
        "configured_hardware": 3,
        "configured_software": 4,
        "configured_per_user": 2,
        "effective_hardware": 3,
        "effective_software": 4,
        "effective_per_user": 2,
    }


async def test_transcode_capabilities_are_visible_only_to_administrators(app, client) -> None:
    user = await create_user(app)
    admin = await create_user(app, username="admin", role=UserRole.ADMIN)
    app.state.hardware_capabilities = HardwareCapabilities(
        methods=(
            HardwareCapability(
                HardwareAcceleration.NVENC,
                (CodecCapability(VideoCodec.H264, "3840x2160"),),
                Path("/dev/nvidia0"),
            ),
        ),
        rejections=(HardwareRejection(HardwareAcceleration.QSV, "no render device is available"),),
        nvidia_gpus=("NVIDIA RTX",),
    )
    await login(client, user.username, "correct horse battery staple")

    forbidden = await client.get("/api/admin/transcode/capabilities")

    assert forbidden.status_code == 403
    await login(client, admin.username, "correct horse battery staple")
    capabilities = await client.get("/api/admin/transcode/capabilities")

    assert capabilities.status_code == 200
    assert capabilities.json() == {
        "methods": [
            {
                "acceleration": "nvenc",
                "codecs": [{"codec": "h264", "maximum_tested_resolution": "3840x2160"}],
            }
        ],
        "rejections": [{"acceleration": "qsv", "reason": "no render device is available"}],
        "nvidia_gpus": ["NVIDIA RTX"],
    }


async def test_admin_can_diagnose_sanitized_transcode_failures(app, client, tmp_path: Path) -> None:
    from pornarr_media.sessions import TranscodeSessionRegistry

    app.state.settings = app.state.settings.model_copy(update={"data_path": tmp_path})
    registry = TranscodeSessionRegistry(app.state.redis, app.state.settings.transcode_path)
    app.state.transcode_sessions = registry
    user = await create_user(app)
    admin = await create_user(app, username="admin", role=UserRole.ADMIN)
    session_id = uuid4()
    directory = app.state.settings.transcode_path / str(session_id)
    directory.mkdir(parents=True)
    session = await registry.register(session_id, user.id, uuid4(), "hls", FakeTranscode(directory))
    await registry.record_failure(session, exit_code=23)
    await login(client, admin.username, "correct horse battery staple")

    failures = await client.get("/api/admin/transcode/failures")

    assert failures.status_code == 200
    failure = failures.json()[0]
    assert failure["session_id"] == str(session_id)
    assert failure["exit_code"] == 23
    assert failure["reason"] == "FFmpeg exited with status 23"
    assert "command" not in failure
    assert "path" not in failure
