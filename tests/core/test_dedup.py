"""Duplicate-detection examples."""

from __future__ import annotations

from datetime import date

from pornarr_core.dedup import (
    DuplicateClassification,
    DuplicateSignal,
    MediaCandidate,
    detect_duplicate,
)


def test_same_file_under_different_names_is_an_exact_duplicate() -> None:
    result = detect_duplicate(
        MediaCandidate(
            title="New name",
            studio="Studio",
            release_date=date(2026, 8, 7),
            duration_seconds=100,
            oshash="same-hash",
        ),
        MediaCandidate(
            title="Old name",
            studio="Other Studio",
            release_date=date(2020, 1, 1),
            duration_seconds=30,
            oshash="same-hash",
        ),
    )

    assert result.classification is DuplicateClassification.DUPLICATE
    assert result.signals == frozenset({DuplicateSignal.OSHASH})


def test_all_fuzzy_signals_produce_an_upgrade_candidate() -> None:
    result = detect_duplicate(
        MediaCandidate(
            title="Example Scene!",
            studio="Example Studio",
            release_date=date(2026, 8, 9),
            duration_seconds=105,
        ),
        MediaCandidate(
            title="Example Scene",
            studio="example studio",
            release_date=date(2026, 8, 7),
            duration_seconds=100,
        ),
    )

    assert result.classification is DuplicateClassification.UPGRADE_CANDIDATE
    assert result.signals == frozenset(
        {
            DuplicateSignal.TITLE,
            DuplicateSignal.STUDIO,
            DuplicateSignal.RELEASE_DATE,
            DuplicateSignal.DURATION,
        }
    )


def test_different_scenes_from_the_same_studio_on_the_same_day_stay_distinct() -> None:
    result = detect_duplicate(
        MediaCandidate(
            title="Sunrise",
            studio="Example Studio",
            release_date=date(2026, 8, 7),
            duration_seconds=100,
        ),
        MediaCandidate(
            title="Nightfall",
            studio="Example Studio",
            release_date=date(2026, 8, 7),
            duration_seconds=100,
        ),
    )

    assert result.classification is DuplicateClassification.DISTINCT
    assert result.signals == frozenset(
        {DuplicateSignal.STUDIO, DuplicateSignal.RELEASE_DATE, DuplicateSignal.DURATION}
    )


def test_missing_or_out_of_range_fuzzy_metadata_is_not_a_candidate() -> None:
    result = detect_duplicate(
        MediaCandidate(
            title="Example Scene!",
            studio=None,
            release_date=date(2026, 8, 10),
            duration_seconds=106,
        ),
        MediaCandidate(
            title="Example Scene",
            studio="Example Studio",
            release_date=date(2026, 8, 7),
            duration_seconds=100,
        ),
    )

    assert result.classification is DuplicateClassification.DISTINCT
    assert result.signals == frozenset({DuplicateSignal.TITLE})
