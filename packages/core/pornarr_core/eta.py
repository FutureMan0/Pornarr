"""Protocol-aware, deliberately ranged download estimates."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Iterable


class Confidence(StrEnum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    UNKNOWN = "unknown"


class Protocol(StrEnum):
    TORRENT = "torrent"
    USENET = "usenet"


@dataclass(frozen=True, slots=True)
class Estimate:
    low_seconds: int | None
    high_seconds: int | None
    confidence: Confidence

    @property
    def is_unknown(self) -> bool:
        return self.low_seconds is None


@dataclass(frozen=True, slots=True)
class Job:
    priority: int
    remaining_seconds: int


def running_estimate(
    *, client_seconds: int | None, remaining_bytes: int, average_bytes_per_second: int
) -> Estimate:
    """Prefer a download client's own estimate, then a one-minute moving average."""
    if client_seconds is not None:
        return _range(client_seconds, Confidence.HIGH)
    if average_bytes_per_second <= 0:
        return unknown()
    return _range(remaining_bytes / average_bytes_per_second, Confidence.MEDIUM)


def search_estimate(
    protocol: Protocol, size_bytes: int, line_bytes_per_second: int, *, seeders: int | None
) -> Estimate:
    """Estimate a release before it is a job; zero-seeder torrents stay unknown."""
    if line_bytes_per_second <= 0 or (protocol is Protocol.TORRENT and not seeders):
        return unknown()
    factor = 1 if protocol is Protocol.USENET else min(seeders / 10, 1)
    return _range(size_bytes / (line_bytes_per_second * factor), Confidence.LOW)


def queued_estimate(*, priority: int, waiting: Iterable[Job]) -> Estimate:
    """Sum jobs that run before this priority, then report the result as a range."""
    seconds = sum(job.remaining_seconds for job in waiting if job.priority >= priority)
    return _range(seconds, Confidence.MEDIUM)


def total_estimate(transfer: Estimate, *, unpack_seconds: int, import_seconds: int) -> Estimate:
    """Add measured post-download work without converting an unknown ETA to a number."""
    if transfer.is_unknown:
        return transfer
    assert transfer.low_seconds is not None and transfer.high_seconds is not None
    return Estimate(
        low_seconds=transfer.low_seconds + unpack_seconds + import_seconds,
        high_seconds=transfer.high_seconds + unpack_seconds + import_seconds,
        confidence=transfer.confidence,
    )


def unknown() -> Estimate:
    return Estimate(low_seconds=None, high_seconds=None, confidence=Confidence.UNKNOWN)


def _range(seconds: float, confidence: Confidence) -> Estimate:
    return Estimate(
        low_seconds=int(seconds * 0.8), high_seconds=int(seconds * 1.2), confidence=confidence
    )
