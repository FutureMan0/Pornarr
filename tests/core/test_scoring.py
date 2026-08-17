"""Pure recommendation-score behaviour."""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from pornarr_core.scoring import (
    RecommendationCandidate,
    RecommendationWeights,
    UserInterestProfile,
    score_recommendation,
)


def test_score_returns_each_weighted_component_and_their_sum() -> None:
    today = date(2026, 8, 11)
    candidate = RecommendationCandidate(
        tags=frozenset({"tag-a"}),
        performers=frozenset({"performer-a"}),
        studio="studio-a",
        quality="1080p",
        release_date=today,
        popularity=0.7,
        rating=0.5,
    )
    profile = UserInterestProfile(
        tags={"tag-a": 0.8},
        performers={"performer-a": 0.6},
        studios={"studio-a": 0.4},
        qualities={"1080p": 0.5},
    )

    result = score_recommendation(candidate, profile, now=today)

    assert result.blocked is False
    assert result.breakdown == {
        "tag": pytest.approx(0.24),
        "performer": pytest.approx(0.15),
        "studio": pytest.approx(0.06),
        "quality": pytest.approx(0.05),
        "recency": pytest.approx(0.1),
        "popularity": pytest.approx(0.07),
        "rating": pytest.approx(0.05),
    }
    assert result.score == pytest.approx(sum(result.breakdown.values()))


@pytest.mark.parametrize(
    ("candidate", "profile", "reason"),
    [
        (
            RecommendationCandidate(tags=frozenset({"blocked"})),
            UserInterestProfile(blocked_tags=frozenset({"blocked"})),
            "tag",
        ),
        (
            RecommendationCandidate(performers=frozenset({"blocked"})),
            UserInterestProfile(blocked_performers=frozenset({"blocked"})),
            "performer",
        ),
    ],
)
def test_hard_blocks_make_a_candidate_unreachable(
    candidate: RecommendationCandidate, profile: UserInterestProfile, reason: str
) -> None:
    result = score_recommendation(candidate, profile)

    assert result.score == 0
    assert result.blocked is True
    assert result.blocked_reason == reason
    assert result.breakdown == {
        "tag": 0,
        "performer": 0,
        "studio": 0,
        "quality": 0,
        "recency": 0,
        "popularity": 0,
        "rating": 0,
    }


def test_weights_change_the_ranking_without_changing_the_candidate() -> None:
    candidate = RecommendationCandidate(tags=frozenset({"tag-a"}))
    profile = UserInterestProfile(tags={"tag-a": 1})

    default_score = score_recommendation(candidate, profile).score
    tuned_score = score_recommendation(
        candidate, profile, weights=RecommendationWeights(tag=0.8)
    ).score

    assert default_score == pytest.approx(0.3)
    assert tuned_score == pytest.approx(0.8)


def test_recency_and_popularity_stay_within_their_normalized_ranges() -> None:
    today = date(2026, 8, 11)
    candidate = RecommendationCandidate(release_date=today - timedelta(days=730), popularity=2)

    result = score_recommendation(candidate, UserInterestProfile(), now=today)

    assert result.breakdown["recency"] == 0
    assert result.breakdown["popularity"] == pytest.approx(0.1)


def test_dated_candidates_need_an_explicit_reference_date() -> None:
    with pytest.raises(ValueError, match="now is required"):
        score_recommendation(
            RecommendationCandidate(release_date=date(2026, 8, 11)), UserInterestProfile()
        )


@pytest.mark.parametrize("weight", [-0.1, float("nan")])
def test_weights_must_be_non_negative_finite_values(weight: float) -> None:
    with pytest.raises(ValueError):
        RecommendationWeights(tag=weight)
