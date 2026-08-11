"""Pure auto-download score behaviour."""

from __future__ import annotations

import pytest

from pornarr_core.automation import AutomationScoreInput, AutomationWeights, score_auto_download


def test_score_breakdown_sums_to_the_reported_score() -> None:
    result = score_auto_download(
        AutomationScoreInput(
            recommendation_score=0.8,
            metadata_quality=0.7,
            release_quality=0.6,
            indexer_reliability=0.9,
            recency=0.5,
            size=0.2,
            duplicate_risk=0.1,
            expected_download_time=0.3,
        )
    )

    assert result.score == pytest.approx(sum(result.breakdown.values()))
    assert result.breakdown == {
        "recommendation_score": pytest.approx(0.24),
        "metadata_quality": pytest.approx(0.105),
        "release_quality": pytest.approx(0.09),
        "indexer_reliability": pytest.approx(0.09),
        "recency": pytest.approx(0.05),
        "size": pytest.approx(-0.01),
        "duplicate_risk": pytest.approx(-0.01),
        "expected_download_time": pytest.approx(-0.015),
    }


def test_small_high_confidence_release_scores_above_large_low_confidence_release() -> None:
    strong = score_auto_download(
        AutomationScoreInput(
            recommendation_score=0.9,
            metadata_quality=0.9,
            release_quality=0.8,
            indexer_reliability=0.9,
            recency=0.8,
            size=0.1,
            duplicate_risk=0,
            expected_download_time=0.1,
        )
    )
    weak = score_auto_download(
        AutomationScoreInput(
            recommendation_score=0.2,
            metadata_quality=0.1,
            release_quality=0.2,
            indexer_reliability=0.3,
            recency=0.1,
            size=1,
            duplicate_risk=0.8,
            expected_download_time=1,
        )
    )

    assert strong.score > weak.score


def test_weights_are_tunable_without_changing_the_formula() -> None:
    result = score_auto_download(
        AutomationScoreInput(
            recommendation_score=1,
            metadata_quality=0,
            release_quality=0,
            indexer_reliability=0,
            recency=0,
            size=0,
            duplicate_risk=0,
            expected_download_time=0,
        ),
        weights=AutomationWeights(recommendation_score=0.75),
    )

    assert result.score == 0.75


@pytest.mark.parametrize("value", (-0.01, 1.01))
def test_score_rejects_non_normalized_signal(value: float) -> None:
    with pytest.raises(ValueError, match="recommendation_score"):
        score_auto_download(
            AutomationScoreInput(
                recommendation_score=value,
                metadata_quality=0,
                release_quality=0,
                indexer_reliability=0,
                recency=0,
                size=0,
                duplicate_risk=0,
                expected_download_time=0,
            )
        )
