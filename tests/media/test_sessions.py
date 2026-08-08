"""Redis-backed transcode session lifecycle."""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from pornarr_media.transcode import HlsPaths


class RecordingRedis:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.members: dict[str, set[str]] = {}
        self.expirations: list[tuple[str, int]] = []

    async def get(self, key: str) -> str | None:
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

    async def sscan_iter(self, key: str) -> AsyncIterator[str]:
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
