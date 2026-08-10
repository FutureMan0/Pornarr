"""Small adapter interface for configured download clients."""

from __future__ import annotations

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
    SEEDING = "seeding"
    STALLED = "stalled"
    UNKNOWN = "unknown"


class DownloadClientAdapter(Protocol):
    async def test_connection(
        self, *, host: str, port: int, url_base: str, credentials: str
    ) -> None: ...
