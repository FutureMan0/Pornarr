"""Cheap validation before an import performs expensive work."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from pornarr_worker.jobs.import_intake import IntakeDecision, IntakeReason, validate_import_file


@pytest.mark.parametrize(
    ("name", "size", "decision", "reason"),
    [
        ("feature.mkv", 60 * 1024**2, IntakeDecision.ACCEPT, None),
        ("sample-feature.mkv", 60 * 1024**2, IntakeDecision.REJECT, IntakeReason.SAMPLE),
        ("feature.txt", 60 * 1024**2, IntakeDecision.REJECT, IntakeReason.UNSUPPORTED_EXTENSION),
        ("feature.rar", 60 * 1024**2, IntakeDecision.REJECT, IntakeReason.ARCHIVE),
        ("feature.mkv", 0, IntakeDecision.REJECT, IntakeReason.EMPTY),
        ("feature.mkv", 1024, IntakeDecision.REJECT, IntakeReason.TOO_SMALL),
    ],
)
async def test_intake_rejects_invalid_files_with_structured_reasons(
    tmp_path: Path,
    name: str,
    size: int,
    decision: IntakeDecision,
    reason: IntakeReason | None,
) -> None:
    path = tmp_path / name
    path.write_bytes(b"x" * size)

    result = await validate_import_file(path, stability_seconds=0)

    assert (result.decision, result.reason) == (decision, reason)


async def test_intake_retries_a_file_that_changes_during_stability_check(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "feature.mkv"
    path.write_bytes(b"x" * (60 * 1024**2))
    original_stat = Path.stat
    calls = 0

    def changing_stat(target: Path, *args, **kwargs):
        nonlocal calls
        calls += 1
        result = original_stat(target, *args, **kwargs)
        if calls == 2:
            return SimpleNamespace(st_size=result.st_size + 1)
        return result

    monkeypatch.setattr(Path, "stat", changing_stat)

    result = await validate_import_file(path, stability_seconds=0)

    assert (result.decision, result.reason) == (IntakeDecision.RETRY, IntakeReason.WRITING)


async def test_intake_rejects_extras_and_a_failed_optional_malware_scan(tmp_path: Path) -> None:
    extra = tmp_path / "feature-trailer.mkv"
    scanned = tmp_path / "feature.mkv"
    extra.write_bytes(b"x" * (60 * 1024**2))
    scanned.write_bytes(b"x" * (60 * 1024**2))

    async def infected(path: Path) -> bool:
        assert path == scanned
        return False

    extra_result = await validate_import_file(extra, stability_seconds=0)
    malware_result = await validate_import_file(
        scanned, stability_seconds=0, malware_scanner=infected
    )

    assert (extra_result.decision, extra_result.reason) == (
        IntakeDecision.REJECT,
        IntakeReason.EXTRA,
    )
    assert (malware_result.decision, malware_result.reason) == (
        IntakeDecision.REJECT,
        IntakeReason.MALWARE,
    )
