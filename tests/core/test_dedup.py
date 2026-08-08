"""Duplicate-detection examples."""

from __future__ import annotations

from datetime import UTC, date, datetime

from pornarr_core.dedup import (
    DuplicateClassification,
    DuplicateSignal,
    MediaCandidate,
    ReleaseCandidate,
    deduplicate_releases,
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


def candidate(
    key: str,
    *,
    title: str = "Example Release 1080p",
    size: int | None = 1_000,
    published_at: datetime | None = datetime(2024, 1, 1, tzinfo=UTC),
    info_hash: str | None = None,
    priority: int = 0,
    healthy: bool = True,
    completeness: int = 0,
) -> ReleaseCandidate:
    return ReleaseCandidate(
        key=key,
        title=title,
        size=size,
        published_at=published_at,
        info_hash=info_hash,
        priority=priority,
        healthy=healthy,
        completeness=completeness,
    )


def test_info_hash_groups_releases_and_keeps_the_best_healthy_source() -> None:
    high_priority = candidate("priority", info_hash="abc", priority=1, completeness=2)
    detailed = candidate("detailed", info_hash="ABC", priority=2, completeness=9)
    unhealthy = candidate("unhealthy", info_hash="abc", priority=0, healthy=False, completeness=10)

    groups = deduplicate_releases((detailed, unhealthy, high_priority))

    assert len(groups) == 1
    assert groups[0].primary == high_priority
    assert groups[0].alternates == (detailed, unhealthy)


def test_title_size_and_age_fallback_groups_only_compatible_releases() -> None:
    first = candidate("first", title="Example---Release", size=1_000, priority=0)
    close = candidate(
        "close",
        title="example release",
        size=1_049,
        published_at=datetime(2024, 1, 2, tzinfo=UTC),
        priority=1,
    )
    different_size = candidate("different-size", title="example release", size=1_053, priority=2)
    different_age = candidate(
        "different-age",
        title="example release",
        published_at=datetime(2024, 1, 4, tzinfo=UTC),
        priority=3,
    )

    groups = deduplicate_releases((first, close, different_size, different_age))

    assert [
        (group.primary.key, tuple(item.key for item in group.alternates)) for group in groups
    ] == [
        ("first", ("close",)),
        ("different-size", ()),
        ("different-age", ()),
    ]


def test_different_hashes_and_incomplete_metadata_never_merge() -> None:
    first = candidate("first", info_hash="first")
    different_hash = candidate("different-hash", info_hash="second")
    incomplete = candidate("incomplete", info_hash=None, size=None)

    groups = deduplicate_releases((first, different_hash, incomplete))

    assert [group.primary.key for group in groups] == [
        "different-hash",
        "first",
        "incomplete",
    ]


def test_age_tolerance_accepts_naive_and_aware_timestamps() -> None:
    naive = candidate("naive", published_at=datetime.fromisoformat("2024-01-01"))
    aware = candidate("aware", published_at=datetime(2024, 1, 1, 1, tzinfo=UTC))

    groups = deduplicate_releases((naive, aware))

    assert groups[0].primary == aware
    assert groups[0].alternates == (naive,)
