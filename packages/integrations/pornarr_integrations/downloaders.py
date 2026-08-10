"""Small adapter interface for configured download clients."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol


class DownloadState(StrEnum):
    """Internal states reported by download-client adapters."""

    CHECKING = "checking"
    COMPLETED = "completed"
    DOWNLOADING = "downloading"
    FAILED = "failed"
    IMPORTING = "importing"
    METADATA = "metadata"
    MOVING = "moving"
    PAUSED = "paused"
    QUEUED = "queued"
    REPAIRING = "repairing"
    REMOVED = "removed"
    SEEDING = "seeding"
    STALLED = "stalled"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class DownloadClientJob:
    """Protocol-neutral state from one client-wide polling operation."""

    client_job_id: str
    state: DownloadState
    size_bytes: int | None
    remaining_bytes: int | None
    download_speed_bytes: int | None
    estimated_seconds: int | None
    error: str | None = None


class DownloadClientAdapter(Protocol):
    async def test_connection(
        self, *, host: str, port: int, url_base: str, credentials: str
    ) -> None: ...


class DownloadClientPollingAdapter(Protocol):
    async def list_jobs(
        self,
        *,
        host: str,
        port: int,
        url_base: str,
        credentials: str,
    ) -> list[DownloadClientJob]: ...
