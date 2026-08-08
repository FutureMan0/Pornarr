"""Cheap, structured file validation before import work starts."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Protocol

MINIMUM_SIZE_BYTES = 50 * 1024**2
VIDEO_EXTENSIONS = frozenset({".avi", ".m4v", ".mkv", ".mov", ".mp4", ".webm", ".wmv"})
ARCHIVE_EXTENSIONS = frozenset({".7z", ".rar", ".zip"})
INCOMPLETE_EXTENSIONS = frozenset({".!qb", ".crdownload", ".part", ".tmp"})
EXTRA_TOKENS = frozenset({"bonus", "extra", "extras", "featurette", "sample", "trailer"})


class IntakeDecision(StrEnum):
    ACCEPT = "accept"
    REJECT = "reject"
    RETRY = "retry"


class IntakeReason(StrEnum):
    ARCHIVE = "archive"
    EMPTY = "empty"
    EXTRA = "extra"
    MALWARE = "malware"
    SAMPLE = "sample"
    TOO_SMALL = "too_small"
    UNSUPPORTED_EXTENSION = "unsupported_extension"
    WRITING = "writing"


class MalwareScanner(Protocol):
    async def __call__(self, path: Path) -> bool: ...


@dataclass(frozen=True)
class IntakeResult:
    decision: IntakeDecision
    reason: IntakeReason | None = None


async def validate_import_file(
    path: Path,
    *,
    stability_seconds: float = 1,
    malware_scanner: MalwareScanner | None = None,
) -> IntakeResult:
    """Classify a candidate file without probing, hashing, or moving it."""
    suffix = path.suffix.lower()
    if suffix in ARCHIVE_EXTENSIONS:
        return IntakeResult(IntakeDecision.REJECT, IntakeReason.ARCHIVE)
    if suffix in INCOMPLETE_EXTENSIONS:
        return IntakeResult(IntakeDecision.RETRY, IntakeReason.WRITING)
    if suffix not in VIDEO_EXTENSIONS:
        return IntakeResult(IntakeDecision.REJECT, IntakeReason.UNSUPPORTED_EXTENSION)
    before = path.stat().st_size
    if before == 0:
        return IntakeResult(IntakeDecision.REJECT, IntakeReason.EMPTY)
    if before < MINIMUM_SIZE_BYTES:
        return IntakeResult(IntakeDecision.REJECT, IntakeReason.TOO_SMALL)
    if "sample" in _tokens(path.stem):
        return IntakeResult(IntakeDecision.REJECT, IntakeReason.SAMPLE)
    if _tokens(path.stem) & EXTRA_TOKENS:
        return IntakeResult(IntakeDecision.REJECT, IntakeReason.EXTRA)
    await asyncio.sleep(stability_seconds)
    if path.stat().st_size != before:
        return IntakeResult(IntakeDecision.RETRY, IntakeReason.WRITING)
    if malware_scanner is not None and not await malware_scanner(path):
        return IntakeResult(IntakeDecision.REJECT, IntakeReason.MALWARE)
    return IntakeResult(IntakeDecision.ACCEPT)


def _tokens(value: str) -> set[str]:
    return set(value.lower().replace("-", " ").replace("_", " ").replace(".", " ").split())
