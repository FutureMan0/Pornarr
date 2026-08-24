from datetime import UTC, date, datetime
from difflib import SequenceMatcher

import pytest

from pornarr_core.dedup import (
    DuplicateClassification,
    DuplicateSignal,
    IndexedRelease,
    MediaCandidate,
    deduplicate,
    detect_duplicate,
)
from pornarr_integrations.indexers import Release


def release(
    guid: str,
    *,
    info_hash: str | None = None,
    title: str = "Sample 1080p",
    size: int = 1_000,
) -> Release:
    return Release(
        guid=guid,
        title=title,
        details_url=None,
        download_url="url",
        published_at=datetime(2026, 1, 1, tzinfo=UTC),
        size=size,
        categories=(),
        info_hash=info_hash,
    )


def test_keeps_best_indexer_and_exposes_same_hash_alternates() -> None:
    groups = deduplicate(
        [
            IndexedRelease("low", 10, release("a", info_hash="abc")),
            IndexedRelease("best", 1, release("b", info_hash="ABC")),
            IndexedRelease("other", 5, release("c", info_hash="abc")),
        ]
    )
    assert groups[0].primary.indexer_id == "best"
    assert [item.indexer_id for item in groups[0].alternates] == ["other", "low"]


def test_groups_small_size_variance_but_not_distinct_releases() -> None:
    groups = deduplicate(
        [
            IndexedRelease("one", 1, release("a")),
            IndexedRelease("two", 2, release("b", size=1_020)),
            IndexedRelease("three", 3, release("c", title="Other", size=1_020)),
        ]
    )
    assert len(groups) == 2


# --- Import-time duplicate detection (docs/pipelines/import.md step 3) --------
#
# The document states four thresholds and one certainty. Each row below sits
# deliberately close to one boundary, on one side of it, with the other three
# dimensions held inside their own limits, so a row that changes verdict names
# exactly which threshold moved. A threshold with no near-miss row on either
# side is not tested.

BASE_TITLE = "the midnight session"
BASE_DATE = date(2026, 3, 4)
BASE_DURATION = 1_200.0
BASE_STUDIO = "Gauntlet Studio"

# `SequenceMatcher` ratios against BASE_TITLE, measured, not guessed. The
# comparison is `> 0.85`, so the exactly-0.85 row must not fire.
TITLE_JUST_ABOVE = "the moonlight session"  # 0.8780
TITLE_EXACTLY_AT = "the midnigXt sesXioX"  # 0.8500
TITLE_JUST_BELOW = "the midnight seance"  # 0.8205


def candidate(
    *,
    title: str = BASE_TITLE,
    studio: str | None = BASE_STUDIO,
    release_date: date | None = BASE_DATE,
    duration_seconds: float | None = BASE_DURATION,
    oshash: str | None = None,
) -> MediaCandidate:
    return MediaCandidate(
        title=title,
        studio=studio,
        release_date=release_date,
        duration_seconds=duration_seconds,
        oshash=oshash,
    )


@pytest.mark.parametrize(
    ("measured", "ratio"),
    [(TITLE_JUST_ABOVE, 0.8780), (TITLE_EXACTLY_AT, 0.8500), (TITLE_JUST_BELOW, 0.8205)],
)
def test_the_boundary_rows_really_do_straddle_the_documented_similarity(
    measured: str, ratio: float
) -> None:
    """The rows below are only near-misses if the metric says they are."""
    assert SequenceMatcher(None, BASE_TITLE, measured).ratio() == pytest.approx(ratio, abs=5e-5)


@pytest.mark.parametrize(
    ("name", "incoming", "existing", "classification", "signals"),
    [
        (
            "an identical fingerprint is certain whatever else disagrees",
            candidate(title="something else", studio="Other", release_date=None, oshash="abc123"),
            candidate(oshash="abc123"),
            DuplicateClassification.DUPLICATE,
            {DuplicateSignal.OSHASH},
        ),
        (
            "all four signals inside their thresholds is an upgrade candidate",
            candidate(
                title=TITLE_JUST_ABOVE, release_date=date(2026, 3, 6), duration_seconds=1_140.0
            ),
            candidate(),
            DuplicateClassification.UPGRADE_CANDIDATE,
            {
                DuplicateSignal.TITLE,
                DuplicateSignal.STUDIO,
                DuplicateSignal.RELEASE_DATE,
                DuplicateSignal.DURATION,
            },
        ),
        (
            "a similarity of exactly 0.85 is not above 0.85",
            candidate(title=TITLE_EXACTLY_AT),
            candidate(),
            DuplicateClassification.DISTINCT,
            {DuplicateSignal.STUDIO, DuplicateSignal.RELEASE_DATE, DuplicateSignal.DURATION},
        ),
        (
            "a similarity just below the threshold drops the title signal",
            candidate(title=TITLE_JUST_BELOW),
            candidate(),
            DuplicateClassification.DISTINCT,
            {DuplicateSignal.STUDIO, DuplicateSignal.RELEASE_DATE, DuplicateSignal.DURATION},
        ),
        (
            "two days apart is within two days",
            candidate(release_date=date(2026, 3, 6)),
            candidate(),
            DuplicateClassification.UPGRADE_CANDIDATE,
            {
                DuplicateSignal.TITLE,
                DuplicateSignal.STUDIO,
                DuplicateSignal.RELEASE_DATE,
                DuplicateSignal.DURATION,
            },
        ),
        (
            "two days apart in the other direction counts the same",
            candidate(release_date=date(2026, 3, 2)),
            candidate(),
            DuplicateClassification.UPGRADE_CANDIDATE,
            {
                DuplicateSignal.TITLE,
                DuplicateSignal.STUDIO,
                DuplicateSignal.RELEASE_DATE,
                DuplicateSignal.DURATION,
            },
        ),
        (
            "three days apart is not",
            candidate(release_date=date(2026, 3, 7)),
            candidate(),
            DuplicateClassification.DISTINCT,
            {DuplicateSignal.TITLE, DuplicateSignal.STUDIO, DuplicateSignal.DURATION},
        ),
        (
            "a difference of exactly five percent is within five percent",
            candidate(duration_seconds=1_140.0),
            candidate(),
            DuplicateClassification.UPGRADE_CANDIDATE,
            {
                DuplicateSignal.TITLE,
                DuplicateSignal.STUDIO,
                DuplicateSignal.RELEASE_DATE,
                DuplicateSignal.DURATION,
            },
        ),
        (
            "a difference a second past five percent is not",
            candidate(duration_seconds=1_139.0),
            candidate(),
            DuplicateClassification.DISTINCT,
            {DuplicateSignal.TITLE, DuplicateSignal.STUDIO, DuplicateSignal.RELEASE_DATE},
        ),
        (
            # 62s over 1262 is 4.91%; over 1200 it would be 5.17%. The row only
            # passes if the denominator is the longer of the two runtimes.
            "the five percent is measured against the longer of the two",
            candidate(duration_seconds=1_262.0),
            candidate(),
            DuplicateClassification.UPGRADE_CANDIDATE,
            {
                DuplicateSignal.TITLE,
                DuplicateSignal.STUDIO,
                DuplicateSignal.RELEASE_DATE,
                DuplicateSignal.DURATION,
            },
        ),
        (
            "and two seconds further out is past it",
            candidate(duration_seconds=1_264.0),
            candidate(),
            DuplicateClassification.DISTINCT,
            {DuplicateSignal.TITLE, DuplicateSignal.STUDIO, DuplicateSignal.RELEASE_DATE},
        ),
        (
            "the same studio in another case and with stray spacing still matches",
            candidate(studio="  gauntlet STUDIO "),
            candidate(),
            DuplicateClassification.UPGRADE_CANDIDATE,
            {
                DuplicateSignal.TITLE,
                DuplicateSignal.STUDIO,
                DuplicateSignal.RELEASE_DATE,
                DuplicateSignal.DURATION,
            },
        ),
        (
            "a different studio is a different scene however alike the rest is",
            candidate(studio="Another Studio"),
            candidate(),
            DuplicateClassification.DISTINCT,
            {DuplicateSignal.TITLE, DuplicateSignal.RELEASE_DATE, DuplicateSignal.DURATION},
        ),
        (
            "an unknown studio is never assumed to be the same studio",
            candidate(studio=None),
            candidate(),
            DuplicateClassification.DISTINCT,
            {DuplicateSignal.TITLE, DuplicateSignal.RELEASE_DATE, DuplicateSignal.DURATION},
        ),
        (
            "an unknown release date is never assumed to be within two days",
            candidate(release_date=None),
            candidate(),
            DuplicateClassification.DISTINCT,
            {DuplicateSignal.TITLE, DuplicateSignal.STUDIO, DuplicateSignal.DURATION},
        ),
        (
            "an unknown duration is never assumed to be within five percent",
            candidate(duration_seconds=None),
            candidate(),
            DuplicateClassification.DISTINCT,
            {DuplicateSignal.TITLE, DuplicateSignal.STUDIO, DuplicateSignal.RELEASE_DATE},
        ),
        (
            "a differing fingerprint falls through to the fuzzy comparison",
            candidate(oshash="aaaa"),
            candidate(oshash="bbbb"),
            DuplicateClassification.UPGRADE_CANDIDATE,
            {
                DuplicateSignal.TITLE,
                DuplicateSignal.STUDIO,
                DuplicateSignal.RELEASE_DATE,
                DuplicateSignal.DURATION,
            },
        ),
    ],
)
def test_the_documented_duplicate_thresholds(
    name: str,
    incoming: MediaCandidate,
    existing: MediaCandidate,
    classification: DuplicateClassification,
    signals: set[DuplicateSignal],
) -> None:
    decision = detect_duplicate(incoming, existing)

    assert (decision.classification, set(decision.signals)) == (classification, signals), name
