"""Quality-decision examples."""

from __future__ import annotations

from pornarr_core.quality import (
    ExistingFile,
    QualityProfile,
    QualityReason,
    QualityVerdict,
    ReleaseCandidate,
    decide_quality,
)


def test_release_outside_the_profile_is_rejected_with_a_reason() -> None:
    decision = decide_quality(
        ReleaseCandidate(quality="WEB 1080p", quality_rank=20),
        QualityProfile(allowed_qualities=frozenset({"WEB 720p"}), cutoff_quality_rank=30),
        existing=None,
    )

    assert (decision.verdict, decision.reason) == (
        QualityVerdict.REJECT,
        QualityReason.NOT_IN_PROFILE,
    )


def test_release_below_the_profile_minimum_score_is_rejected() -> None:
    decision = decide_quality(
        ReleaseCandidate(quality="WEB 1080p", quality_rank=20, custom_format_scores=(5,)),
        QualityProfile(
            allowed_qualities=frozenset({"WEB 1080p"}),
            cutoff_quality_rank=30,
            minimum_custom_format_score=30,
        ),
        existing=None,
    )

    assert (decision.verdict, decision.reason, decision.score) == (
        QualityVerdict.REJECT,
        QualityReason.BELOW_MINIMUM_SCORE,
        25,
    )


def test_first_allowed_release_is_grabbed_with_its_total_score() -> None:
    decision = decide_quality(
        ReleaseCandidate(quality="WEB 1080p", quality_rank=20, custom_format_scores=(5, -2)),
        QualityProfile(allowed_qualities=frozenset({"WEB 1080p"}), cutoff_quality_rank=30),
        existing=None,
    )

    assert (decision.verdict, decision.reason, decision.score) == (
        QualityVerdict.GRAB,
        QualityReason.NO_EXISTING_FILE,
        23,
    )


def test_release_equal_to_the_current_score_is_not_an_upgrade() -> None:
    decision = decide_quality(
        ReleaseCandidate(quality="WEB 1080p", quality_rank=20, custom_format_scores=(5,)),
        QualityProfile(allowed_qualities=frozenset({"WEB 1080p"}), cutoff_quality_rank=30),
        existing=ExistingFile(quality_rank=20, custom_format_score=5),
    )

    assert (decision.verdict, decision.reason) == (
        QualityVerdict.REJECT,
        QualityReason.NOT_AN_UPGRADE,
    )


def test_cutoff_blocks_an_otherwise_better_release() -> None:
    decision = decide_quality(
        ReleaseCandidate(quality="WEB 2160p", quality_rank=30, custom_format_scores=(5,)),
        QualityProfile(
            allowed_qualities=frozenset({"WEB 1080p", "WEB 2160p"}), cutoff_quality_rank=20
        ),
        existing=ExistingFile(quality_rank=20, custom_format_score=0),
    )

    assert (decision.verdict, decision.reason) == (
        QualityVerdict.REJECT,
        QualityReason.CUTOFF_MET,
    )


def test_better_release_below_the_cutoff_is_an_upgrade() -> None:
    decision = decide_quality(
        ReleaseCandidate(quality="WEB 1080p", quality_rank=20, custom_format_scores=(5,)),
        QualityProfile(
            allowed_qualities=frozenset({"WEB 720p", "WEB 1080p"}), cutoff_quality_rank=30
        ),
        existing=ExistingFile(quality_rank=10, custom_format_score=0),
    )

    assert (decision.verdict, decision.reason, decision.score) == (
        QualityVerdict.UPGRADE,
        QualityReason.UPGRADE_AVAILABLE,
        25,
    )
