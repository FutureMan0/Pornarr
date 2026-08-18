"""Redis-backed lifecycle records for running transcodes.

The heartbeat key is the 60-second source of truth for an active session. A
short-lived companion record survives just long enough to stop an orphaned
process after an API restart.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import signal
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Protocol
from uuid import UUID

from pornarr_media.capabilities import HardwareCapabilities
from pornarr_shared.config import Settings
from pornarr_shared.errors import PornarrError

SESSION_TTL_SECONDS = 60
SESSION_RECORD_TTL_SECONDS = SESSION_TTL_SECONDS * 2
SESSION_INDEX_KEY = "pornarr:transcode:sessions"
FAILURE_RECORD_TTL_SECONDS = 7 * 24 * 60 * 60
FAILURE_INDEX_KEY = "pornarr:transcode:failures"
MAX_FAILURE_RECORDS = 20


class RunningTranscode(Protocol):
    """The process lifecycle the registry needs from an HLS transcode."""

    @property
    def process(self) -> ProcessHandle: ...

    async def stop(self) -> None: ...


class ProcessHandle(Protocol):
    @property
    def pid(self) -> int | None: ...


@dataclass(frozen=True, slots=True)
class TranscodeSession:
    id: UUID
    user_id: UUID
    media_id: UUID
    mode: str
    hardware: bool
    process_id: int
    created_at: datetime
    device_label: str | None = None


@dataclass(frozen=True, slots=True)
class TranscodeFailure:
    session_id: UUID
    user_id: UUID
    media_id: UUID
    mode: str
    exit_code: int
    created_at: datetime

    @property
    def reason(self) -> str:
        if self.exit_code < 0:
            return f"FFmpeg was terminated by signal {-self.exit_code}"
        return f"FFmpeg exited with status {self.exit_code}"


def session_key(session_id: UUID) -> str:
    return f"pornarr:transcode:session:{session_id}"


def heartbeat_key(session_id: UUID) -> str:
    return f"{session_key(session_id)}:heartbeat"


class TranscodeMode(StrEnum):
    HARDWARE = "hardware"
    SOFTWARE = "software"


class TranscodeLimitReachedError(PornarrError):
    code = "TRANSCODE_LIMIT_REACHED"
    status = 429


@dataclass(frozen=True, slots=True)
class TranscodeLimits:
    hardware: int
    software: int
    per_user: int

    @classmethod
    def from_settings(
        cls, settings: Settings, capabilities: HardwareCapabilities
    ) -> TranscodeLimits:
        detected_hardware = len(capabilities.nvidia_gpus) or int(bool(capabilities.methods))
        return cls(
            hardware=settings.transcode_max_hw_sessions
            if settings.transcode_max_hw_sessions is not None
            else detected_hardware,
            software=settings.transcode_max_sw_sessions
            if settings.transcode_max_sw_sessions is not None
            else 1,
            per_user=settings.transcode_max_per_user,
        )


def choose_transcode_mode(
    sessions: list[TranscodeSession], user_id: UUID, limits: TranscodeLimits
) -> TranscodeMode:
    if sum(session.user_id == user_id for session in sessions) >= limits.per_user:
        raise TranscodeLimitReachedError(
            "The per-user transcode limit was reached.", limit="per_user"
        )
    if sum(session.hardware for session in sessions) < limits.hardware:
        return TranscodeMode.HARDWARE
    if sum(not session.hardware for session in sessions) < limits.software:
        return TranscodeMode.SOFTWARE
    raise TranscodeLimitReachedError("All transcode slots are occupied.", limit="software")


class TranscodeSessionRegistry:
    """Keep active-session state, cleanup, and ownership behind one interface."""

    def __init__(self, redis: Any, transcode_path: Path) -> None:
        self._redis = redis
        self._transcode_path = transcode_path
        self._processes: dict[UUID, RunningTranscode] = {}
        self._watchers: dict[UUID, asyncio.Task[None]] = {}

    async def register(
        self,
        session_id: UUID,
        user_id: UUID,
        media_id: UUID,
        mode: str,
        transcode: RunningTranscode,
        *,
        hardware: bool = False,
        device_label: str | None = None,
    ) -> TranscodeSession:
        if transcode.process.pid is None:
            raise ValueError("A transcode process must be running before it can be registered")
        session = TranscodeSession(
            id=session_id,
            user_id=user_id,
            media_id=media_id,
            mode=mode,
            hardware=hardware,
            process_id=transcode.process.pid,
            created_at=datetime.now(UTC),
            device_label=device_label,
        )
        await self._redis.set(
            session_key(session_id), _serialize(session), ex=SESSION_RECORD_TTL_SECONDS
        )
        await self._redis.set(heartbeat_key(session_id), "1", ex=SESSION_TTL_SECONDS)
        await self._redis.sadd(SESSION_INDEX_KEY, str(session_id))
        self._processes[session_id] = transcode
        if callable(getattr(transcode.process, "wait", None)):
            self._watchers[session_id] = asyncio.create_task(
                self._watch_process(session, transcode.process)
            )
        return session

    async def get(self, session_id: UUID) -> TranscodeSession | None:
        if await self._redis.get(heartbeat_key(session_id)) is None:
            return None
        return await self._record(session_id)

    async def _record(self, session_id: UUID) -> TranscodeSession | None:
        stored = await self._redis.get(session_key(session_id))
        return _deserialize(stored)

    async def heartbeat(self, session_id: UUID, user_id: UUID) -> TranscodeSession | None:
        session = await self.get(session_id)
        if session is None or session.user_id != user_id:
            return None
        await self._redis.expire(session_key(session_id), SESSION_RECORD_TTL_SECONDS)
        await self._redis.expire(heartbeat_key(session_id), SESSION_TTL_SECONDS)
        return session

    async def active_sessions(self) -> list[TranscodeSession]:
        sessions: list[TranscodeSession] = []
        async for session_id in self._session_ids():
            session = await self.get(session_id)
            if session is None:
                await self._cleanup(session_id)
            else:
                sessions.append(session)
        return sorted(sessions, key=lambda session: session.created_at)

    async def live_sessions(self) -> list[TranscodeSession]:
        """Return heartbeat-backed sessions without changing their lifecycle."""

        sessions: list[TranscodeSession] = []
        async for session_id in self._session_ids():
            session = await self.get(session_id)
            if session is not None:
                sessions.append(session)
        return sorted(sessions, key=lambda session: session.created_at)

    async def session_for(self, user_id: UUID, media_id: UUID) -> TranscodeSession | None:
        """Return this user's live session for one title, if one is running."""

        for session in await self.active_sessions():
            if session.user_id == user_id and session.media_id == media_id:
                return session
        return None

    async def select_mode(self, user_id: UUID, limits: TranscodeLimits) -> TranscodeMode:
        return choose_transcode_mode(await self.active_sessions(), user_id, limits)

    async def terminate(self, session_id: UUID) -> bool:
        if await self._record(session_id) is None:
            return False
        await self._cleanup(session_id)
        return True

    async def reap_expired(self) -> int:
        expired = 0
        async for session_id in self._session_ids():
            if await self.get(session_id) is None:
                await self._cleanup(session_id)
                expired += 1
        return expired

    async def record_failure(self, session: TranscodeSession, *, exit_code: int) -> None:
        failure = TranscodeFailure(
            session_id=session.id,
            user_id=session.user_id,
            media_id=session.media_id,
            mode=session.mode,
            exit_code=exit_code,
            created_at=datetime.now(UTC),
        )
        failures = [failure, *await self.recent_failures()]
        await self._redis.set(
            FAILURE_INDEX_KEY,
            _serialize_failures(failures[:MAX_FAILURE_RECORDS]),
            ex=FAILURE_RECORD_TTL_SECONDS,
        )

    async def recent_failures(self) -> list[TranscodeFailure]:
        return _deserialize_failures(await self._redis.get(FAILURE_INDEX_KEY))

    async def _session_ids(self) -> AsyncIterator[UUID]:
        async for raw_session_id in self._redis.sscan_iter(SESSION_INDEX_KEY):
            try:
                yield UUID(raw_session_id)
            except (TypeError, ValueError):
                await self._redis.srem(SESSION_INDEX_KEY, raw_session_id)

    async def _cleanup(self, session_id: UUID) -> None:
        watcher = self._watchers.pop(session_id, None)
        if watcher is not None and watcher is not asyncio.current_task():
            watcher.cancel()
        transcode = self._processes.pop(session_id, None)
        session = await self._record(session_id) if transcode is None else None
        await self._redis.delete(session_key(session_id))
        await self._redis.delete(heartbeat_key(session_id))
        await self._redis.srem(SESSION_INDEX_KEY, str(session_id))
        if transcode is not None:
            await transcode.stop()
        elif session is not None:
            await asyncio.to_thread(
                _stop_orphaned_transcode,
                session.process_id,
                self._transcode_path / str(session_id),
            )
        await asyncio.to_thread(
            shutil.rmtree, self._transcode_path / str(session_id), ignore_errors=True
        )

    async def _watch_process(self, session: TranscodeSession, process: Any) -> None:
        try:
            exit_code = await process.wait()
            if not isinstance(exit_code, int) or exit_code == 0:
                return
            await self.record_failure(session, exit_code=exit_code)
            await self._cleanup(session.id)
        finally:
            if self._watchers.get(session.id) is asyncio.current_task():
                self._watchers.pop(session.id, None)


def _serialize(session: TranscodeSession) -> str:
    return json.dumps(
        {
            "id": str(session.id),
            "user_id": str(session.user_id),
            "media_id": str(session.media_id),
            "mode": session.mode,
            "hardware": session.hardware,
            "process_id": session.process_id,
            "created_at": session.created_at.isoformat(),
            "device_label": session.device_label,
        }
    )


def _deserialize(stored: str | None) -> TranscodeSession | None:
    if stored is None:
        return None
    try:
        data = json.loads(stored)
        mode = data["mode"]
        hardware = data.get("hardware", False)
        process_id = data["process_id"]
        if (
            not isinstance(mode, str)
            or not isinstance(hardware, bool)
            or not isinstance(process_id, int)
            or process_id <= 0
        ):
            return None
        return TranscodeSession(
            id=UUID(data["id"]),
            user_id=UUID(data["user_id"]),
            media_id=UUID(data["media_id"]),
            mode=mode,
            hardware=hardware,
            process_id=process_id,
            created_at=datetime.fromisoformat(data["created_at"]),
            # Absent on records written before device labels existed.
            device_label=data.get("device_label"),
        )
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        return None


def _serialize_failures(failures: list[TranscodeFailure]) -> str:
    return json.dumps(
        [
            {
                "session_id": str(failure.session_id),
                "user_id": str(failure.user_id),
                "media_id": str(failure.media_id),
                "mode": failure.mode,
                "exit_code": failure.exit_code,
                "created_at": failure.created_at.isoformat(),
            }
            for failure in failures
        ]
    )


def _deserialize_failures(stored: str | None) -> list[TranscodeFailure]:
    if stored is None:
        return []
    try:
        data = json.loads(stored)
    except json.JSONDecodeError:
        return []
    if not isinstance(data, list):
        return []
    failures: list[TranscodeFailure] = []
    for item in data:
        try:
            if not isinstance(item, dict):
                continue
            mode = item["mode"]
            exit_code = item["exit_code"]
            if not isinstance(mode, str) or not isinstance(exit_code, int):
                continue
            failures.append(
                TranscodeFailure(
                    session_id=UUID(item["session_id"]),
                    user_id=UUID(item["user_id"]),
                    media_id=UUID(item["media_id"]),
                    mode=mode,
                    exit_code=exit_code,
                    created_at=datetime.fromisoformat(item["created_at"]),
                )
            )
        except (KeyError, TypeError, ValueError):
            continue
    return failures


def _stop_orphaned_transcode(process_id: int, directory: Path) -> None:
    """Terminate only an FFmpeg process that still targets this session directory."""

    command_path = Path(f"/proc/{process_id}/cmdline")
    try:
        command = command_path.read_bytes()
    except OSError:
        return
    if b"ffmpeg" not in command or os.fsencode(str(directory)) not in command:
        return
    try:
        os.kill(process_id, signal.SIGTERM)
    except ProcessLookupError:
        return
