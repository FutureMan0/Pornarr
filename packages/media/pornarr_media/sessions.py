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
from pathlib import Path
from typing import Any, Protocol
from uuid import UUID

SESSION_TTL_SECONDS = 60
SESSION_RECORD_TTL_SECONDS = SESSION_TTL_SECONDS * 2
SESSION_INDEX_KEY = "pornarr:transcode:sessions"


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
    process_id: int
    created_at: datetime


def session_key(session_id: UUID) -> str:
    return f"pornarr:transcode:session:{session_id}"


def heartbeat_key(session_id: UUID) -> str:
    return f"{session_key(session_id)}:heartbeat"


class TranscodeSessionRegistry:
    """Keep active-session state, cleanup, and ownership behind one interface."""

    def __init__(self, redis: Any, transcode_path: Path) -> None:
        self._redis = redis
        self._transcode_path = transcode_path
        self._processes: dict[UUID, RunningTranscode] = {}

    async def register(
        self,
        session_id: UUID,
        user_id: UUID,
        media_id: UUID,
        mode: str,
        transcode: RunningTranscode,
    ) -> TranscodeSession:
        if transcode.process.pid is None:
            raise ValueError("A transcode process must be running before it can be registered")
        session = TranscodeSession(
            id=session_id,
            user_id=user_id,
            media_id=media_id,
            mode=mode,
            process_id=transcode.process.pid,
            created_at=datetime.now(UTC),
        )
        await self._redis.set(
            session_key(session_id), _serialize(session), ex=SESSION_RECORD_TTL_SECONDS
        )
        await self._redis.set(heartbeat_key(session_id), "1", ex=SESSION_TTL_SECONDS)
        await self._redis.sadd(SESSION_INDEX_KEY, str(session_id))
        self._processes[session_id] = transcode
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

    async def _session_ids(self) -> AsyncIterator[UUID]:
        async for raw_session_id in self._redis.sscan_iter(SESSION_INDEX_KEY):
            try:
                yield UUID(raw_session_id)
            except (TypeError, ValueError):
                await self._redis.srem(SESSION_INDEX_KEY, raw_session_id)

    async def _cleanup(self, session_id: UUID) -> None:
        transcode = self._processes.pop(session_id, None)
        if transcode is not None:
            await transcode.stop()
        else:
            session = await self._record(session_id)
            if session is not None:
                await asyncio.to_thread(
                    _stop_orphaned_transcode,
                    session.process_id,
                    self._transcode_path / str(session_id),
                )
        await asyncio.to_thread(
            shutil.rmtree, self._transcode_path / str(session_id), ignore_errors=True
        )
        await self._redis.delete(session_key(session_id))
        await self._redis.delete(heartbeat_key(session_id))
        await self._redis.srem(SESSION_INDEX_KEY, str(session_id))


def _serialize(session: TranscodeSession) -> str:
    return json.dumps(
        {
            "id": str(session.id),
            "user_id": str(session.user_id),
            "media_id": str(session.media_id),
            "mode": session.mode,
            "process_id": session.process_id,
            "created_at": session.created_at.isoformat(),
        }
    )


def _deserialize(stored: str | None) -> TranscodeSession | None:
    if stored is None:
        return None
    try:
        data = json.loads(stored)
        mode = data["mode"]
        process_id = data["process_id"]
        if not isinstance(mode, str) or not isinstance(process_id, int) or process_id <= 0:
            return None
        return TranscodeSession(
            id=UUID(data["id"]),
            user_id=UUID(data["user_id"]),
            media_id=UUID(data["media_id"]),
            mode=mode,
            process_id=process_id,
            created_at=datetime.fromisoformat(data["created_at"]),
        )
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        return None


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
