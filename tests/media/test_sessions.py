"""Redis-backed transcode session lifecycle."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from pydantic import SecretStr

from pornarr_media.capabilities import HardwareCapabilities
from pornarr_media.transcode import HlsPaths
from pornarr_shared.config import Settings


class RecordingRedis:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.members: dict[str, set[str]] = {}
        self.expirations: list[tuple[str, int]] = []

    async def get(self, key: str) -> str | bytes | None:
        return self.values.get(key)

    async def set(self, key: str, value: str, *, ex: int) -> bool:
        self.values[key] = value
        self.expirations.append((key, ex))
        return True

    async def delete(self, *keys: str) -> int:
        deleted = 0
        for key in keys:
            if key in self.values:
                del self.values[key]
                deleted += 1
            if key in self.members:
                del self.members[key]
                deleted += 1
        return deleted

    async def sadd(self, key: str, *values: str) -> int:
        members = self.members.setdefault(key, set())
        before = len(members)
        members.update(values)
        return len(members) - before

    async def srem(self, key: str, *values: str) -> int:
        members = self.members.setdefault(key, set())
        before = len(members)
        members.difference_update(values)
        return before - len(members)

    async def sscan_iter(self, key: str) -> AsyncIterator[str | bytes]:
        for value in tuple(self.members.get(key, set())):
            yield value

    async def expire(self, key: str, time: int) -> bool:
        self.expirations.append((key, time))
        return True


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


class FailingProcess:
    pid = 1

    def __init__(self) -> None:
        self.wait_started = asyncio.Event()
        self.finish = asyncio.Event()

    async def wait(self) -> int:
        self.wait_started.set()
        await self.finish.wait()
        return 23


class FailingTranscode(FakeTranscode):
    def __init__(self, directory: Path, process: FailingProcess) -> None:
        super().__init__(directory)
        self.process = process
        self.stopped_event = asyncio.Event()

    async def stop(self) -> None:
        await super().stop()
        self.stopped_event.set()


async def test_session_survives_a_registry_restart_and_heartbeat_refreshes_its_ttl(
    tmp_path: Path,
) -> None:
    from pornarr_media.sessions import SESSION_TTL_SECONDS, TranscodeSessionRegistry, heartbeat_key

    redis = RecordingRedis()
    registry = TranscodeSessionRegistry(redis, tmp_path)
    session_id = uuid4()
    user_id = uuid4()
    media_id = uuid4()
    directory = tmp_path / str(session_id)
    directory.mkdir()
    transcode = FakeTranscode(directory)

    registered = await registry.register(session_id, user_id, media_id, "hls", transcode)
    restarted_registry = TranscodeSessionRegistry(redis, tmp_path)

    assert await restarted_registry.active_sessions() == [registered]
    assert await restarted_registry.heartbeat(session_id, user_id) == registered
    assert redis.expirations[-1] == (heartbeat_key(session_id), SESSION_TTL_SECONDS)


async def test_expired_session_stops_ffmpeg_and_removes_its_segments(tmp_path: Path) -> None:
    from pornarr_media.sessions import TranscodeSessionRegistry, heartbeat_key

    redis = RecordingRedis()
    registry = TranscodeSessionRegistry(redis, tmp_path)
    session_id = uuid4()
    directory = tmp_path / str(session_id)
    directory.mkdir()
    (directory / "segment_00000.ts").write_bytes(b"segment")
    transcode = FakeTranscode(directory)
    await registry.register(session_id, uuid4(), uuid4(), "hls", transcode)
    await redis.delete(heartbeat_key(session_id))

    assert await registry.reap_expired() == 1
    assert transcode.stopped is True
    assert not directory.exists()


async def test_restarted_registry_reaps_the_recorded_ffmpeg_process(
    monkeypatch, tmp_path: Path
) -> None:
    import pornarr_media.sessions as sessions

    redis = RecordingRedis()
    session_id = uuid4()
    directory = tmp_path / str(session_id)
    directory.mkdir()
    registry = sessions.TranscodeSessionRegistry(redis, tmp_path)
    await registry.register(session_id, uuid4(), uuid4(), "hls", FakeTranscode(directory))
    restarted_registry = sessions.TranscodeSessionRegistry(redis, tmp_path)
    stopped: list[tuple[int, Path]] = []
    monkeypatch.setattr(
        sessions,
        "_stop_orphaned_transcode",
        lambda process_id, output: stopped.append((process_id, output)),
    )
    await redis.delete(sessions.heartbeat_key(session_id))

    assert await restarted_registry.reap_expired() == 1
    assert stopped == [(1, directory)]
    assert not directory.exists()


async def test_termination_removes_the_session_and_its_hls_files(tmp_path: Path) -> None:
    from pornarr_media.sessions import TranscodeSessionRegistry

    redis = RecordingRedis()
    registry = TranscodeSessionRegistry(redis, tmp_path)
    session_id = uuid4()
    directory = tmp_path / str(session_id)
    directory.mkdir()
    transcode = FakeTranscode(directory)
    await registry.register(session_id, uuid4(), uuid4(), "hls", transcode)

    assert await registry.terminate(session_id) is True
    assert transcode.stopped is True
    assert await registry.active_sessions() == []
    assert not directory.exists()


async def test_failed_ffmpeg_is_recorded_without_its_command_or_log(tmp_path: Path) -> None:
    from pornarr_media.sessions import TranscodeSessionRegistry

    registry = TranscodeSessionRegistry(RecordingRedis(), tmp_path)
    session_id = uuid4()
    directory = tmp_path / str(session_id)
    directory.mkdir()
    process = FailingProcess()
    transcode = FailingTranscode(directory, process)
    await registry.register(session_id, uuid4(), uuid4(), "hls", transcode)
    await asyncio.wait_for(process.wait_started.wait(), timeout=1)
    process.finish.set()
    await asyncio.wait_for(transcode.stopped_event.wait(), timeout=1)

    failures = await registry.recent_failures()

    assert len(failures) == 1
    assert failures[0].session_id == session_id
    assert failures[0].exit_code == 23

    assert await registry.active_sessions() == []


def test_hardware_saturation_falls_back_to_software_and_then_names_the_limit() -> None:
    from pornarr_media.sessions import (
        TranscodeLimitReachedError,
        TranscodeLimits,
        TranscodeMode,
        TranscodeSession,
        choose_transcode_mode,
    )

    user_id = uuid4()
    limits = TranscodeLimits(hardware=1, software=1, per_user=3)
    hardware_session = TranscodeSession(
        uuid4(), uuid4(), uuid4(), "hls", True, 1, datetime.now(UTC)
    )
    software_session = TranscodeSession(
        uuid4(), uuid4(), uuid4(), "hls", False, 2, datetime.now(UTC)
    )

    assert choose_transcode_mode([hardware_session], user_id, limits) == TranscodeMode.SOFTWARE
    user_session = TranscodeSession(uuid4(), user_id, uuid4(), "hls", True, 3, datetime.now(UTC))
    with pytest.raises(TranscodeLimitReachedError) as per_user_error:
        choose_transcode_mode([user_session], user_id, TranscodeLimits(2, 2, 1))
    assert per_user_error.value.as_dict()["context"] == {"limit": "per_user"}
    with pytest.raises(TranscodeLimitReachedError) as error:
        choose_transcode_mode([hardware_session, software_session], user_id, limits)
    assert error.value.as_dict()["context"] == {"limit": "software"}


def test_detection_and_settings_produce_conservative_session_limits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from pornarr_media.sessions import TranscodeLimits

    # Four cores, mocked, so this assertion does not depend on the host it
    # happens to run on.
    monkeypatch.setattr("os.sched_getaffinity", lambda pid: set(range(4)))
    settings = Settings(
        app_secret=SecretStr("a" * 32),
        database_url="postgresql+psycopg://example",
        redis_url="redis://example",
    )
    capabilities = HardwareCapabilities(methods=(), rejections=(), nvidia_gpus=())

    assert TranscodeLimits.from_settings(settings, capabilities) == TranscodeLimits(0, 2, 2)


def test_software_session_default_is_the_cpu_allowance_divided_by_two(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """PRODUCT DEFECT D4. transcode.md L30 documents the software cap as CPU
    cores divided by two, floored at one so a single-core host still gets a
    stream instead of a session cap nothing can ever pass."""
    from pornarr_media.sessions import detected_software_session_default

    monkeypatch.setattr("os.sched_getaffinity", lambda pid: set(range(16)))
    assert detected_software_session_default() == 8

    monkeypatch.setattr("os.sched_getaffinity", lambda pid: {0})
    assert detected_software_session_default() == 1


class UndecodedRedis(RecordingRedis):
    """Redis as arq hands it over: raw bytes, because arq stores packed jobs.

    The API creates its own client with `decode_responses=True`; the worker
    reaches Redis through the arq pool, which does not decode. Both build the
    same registry, so the registry has to accept either.
    """

    async def get(self, key: str) -> str | bytes | None:
        value = self.values.get(key)
        return None if value is None else value.encode()

    async def sscan_iter(self, key: str) -> AsyncIterator[str | bytes]:
        for value in tuple(self.members.get(key, set())):
            yield value.encode()


async def test_a_worker_side_registry_does_not_empty_the_session_index(tmp_path: Path) -> None:
    """One nightly cleanup run must not lose every session that is playing.

    `_session_ids` removes anything it cannot read as a session id. Read
    through a client that does not decode, every id was unreadable, so the
    first pass through `live_sessions` deleted the entire index: the
    administration area went empty, "Playing on" went empty, and the FFmpeg
    processes those records pointed at were left with nobody holding them.
    """

    from pornarr_media.sessions import SESSION_INDEX_KEY, TranscodeSessionRegistry

    redis = UndecodedRedis()
    session_id = uuid4()
    registry = TranscodeSessionRegistry(redis, tmp_path)
    await registry.register(
        session_id, uuid4(), uuid4(), "hls", FakeTranscode(tmp_path / str(session_id))
    )

    live = await registry.live_sessions()

    assert [session.id for session in live] == [session_id]
    assert redis.members[SESSION_INDEX_KEY] == {str(session_id)}
