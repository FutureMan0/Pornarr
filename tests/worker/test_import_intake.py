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


@pytest.mark.parametrize(
    ("what", "name", "size", "decision", "reason"),
    [
        ("a supported container", "feature.mkv", 60 * 1024**2, IntakeDecision.ACCEPT, None),
        ("mp4", "feature.mp4", 60 * 1024**2, IntakeDecision.ACCEPT, None),
        ("webm", "feature.webm", 60 * 1024**2, IntakeDecision.ACCEPT, None),
        (
            "an uppercase extension is still the same extension",
            "feature.MKV",
            60 * 1024**2,
            IntakeDecision.ACCEPT,
            None,
        ),
        ("a rar archive", "feature.rar", 60 * 1024**2, IntakeDecision.REJECT, IntakeReason.ARCHIVE),
        ("a zip archive", "feature.zip", 60 * 1024**2, IntakeDecision.REJECT, IntakeReason.ARCHIVE),
        ("a 7z archive", "feature.7z", 60 * 1024**2, IntakeDecision.REJECT, IntakeReason.ARCHIVE),
        (
            "a qBittorrent part file",
            "feature.mkv.!qb",
            60 * 1024**2,
            IntakeDecision.RETRY,
            IntakeReason.WRITING,
        ),
        (
            "a generic part file",
            "feature.part",
            60 * 1024**2,
            IntakeDecision.RETRY,
            IntakeReason.WRITING,
        ),
        (
            "a browser download in progress",
            "feature.crdownload",
            60 * 1024**2,
            IntakeDecision.RETRY,
            IntakeReason.WRITING,
        ),
        (
            "a temporary file",
            "feature.tmp",
            60 * 1024**2,
            IntakeDecision.RETRY,
            IntakeReason.WRITING,
        ),
        (
            "a subtitle beside the feature",
            "feature.srt",
            60 * 1024**2,
            IntakeDecision.REJECT,
            IntakeReason.UNSUPPORTED_EXTENSION,
        ),
        (
            "no extension at all",
            "feature",
            60 * 1024**2,
            IntakeDecision.REJECT,
            IntakeReason.UNSUPPORTED_EXTENSION,
        ),
        ("an empty file", "feature.mkv", 0, IntakeDecision.REJECT, IntakeReason.EMPTY),
        (
            "one byte below the floor",
            "feature.mkv",
            50 * 1024**2 - 1,
            IntakeDecision.REJECT,
            IntakeReason.TOO_SMALL,
        ),
        (
            "exactly the floor, which is not below it",
            "feature.mkv",
            50 * 1024**2,
            IntakeDecision.ACCEPT,
            None,
        ),
        (
            "a sample as its own word",
            "the.feature.sample.mkv",
            60 * 1024**2,
            IntakeDecision.REJECT,
            IntakeReason.SAMPLE,
        ),
        (
            "a sample separated by a dash",
            "feature-sample.mkv",
            60 * 1024**2,
            IntakeDecision.REJECT,
            IntakeReason.SAMPLE,
        ),
        (
            "a trailer",
            "feature-trailer.mkv",
            60 * 1024**2,
            IntakeDecision.REJECT,
            IntakeReason.EXTRA,
        ),
        (
            "a featurette",
            "feature.featurette.mkv",
            60 * 1024**2,
            IntakeDecision.REJECT,
            IntakeReason.EXTRA,
        ),
        # The negative space: words that merely contain a rejected token are not
        # that token, and intake must not fire on them.
        (
            "a title containing the word sampler",
            "the.sampler.sessions.mkv",
            60 * 1024**2,
            IntakeDecision.ACCEPT,
            None,
        ),
        (
            "a title containing the word extraordinary",
            "extraordinary.evening.mkv",
            60 * 1024**2,
            IntakeDecision.ACCEPT,
            None,
        ),
        (
            "a title containing the word trailers as part of a phrase",
            "trailerpark.nights.mkv",
            60 * 1024**2,
            IntakeDecision.ACCEPT,
            None,
        ),
    ],
)
async def test_intake_classifies_every_documented_case_with_its_reason(
    tmp_path: Path,
    what: str,
    name: str,
    size: int,
    decision: IntakeDecision,
    reason: IntakeReason | None,
) -> None:
    """Step 1: "Extension, size and archive state are checked."""
    path = tmp_path / name
    path.write_bytes(b"x" * size)

    result = await validate_import_file(path, stability_seconds=0)

    assert (result.decision, result.reason) == (decision, reason), what


@pytest.mark.xfail(
    strict=True,
    reason=(
        "DEFECT. `validate_import_file` reads the size and rejects at "
        "import_intake.py:59-63, before the stability wait at :68. A download the "
        "client is still moving into the completed directory is therefore rejected "
        "as `too_small` - permanently, because `rejected` is terminal and the "
        "trigger is keyed by source path - instead of being retried as `writing`, "
        "which is the decision the module already has for exactly this case."
    ),
)
async def test_a_file_still_growing_is_retried_rather_than_called_too_small(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "feature.mkv"
    path.write_bytes(b"x" * (10 * 1024**2))
    original_stat = Path.stat
    calls = 0

    def growing_stat(target: Path, *args, **kwargs):
        nonlocal calls
        result = original_stat(target, *args, **kwargs)
        if target != path:
            return result
        calls += 1
        return SimpleNamespace(st_size=10 * 1024**2 * calls)

    monkeypatch.setattr(Path, "stat", growing_stat)

    result = await validate_import_file(path, stability_seconds=0)

    assert (result.decision, result.reason) == (IntakeDecision.RETRY, IntakeReason.WRITING)
