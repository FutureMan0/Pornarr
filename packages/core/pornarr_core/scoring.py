"""Pure, explainable recommendation scoring."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date
from math import isfinite

_BREAKDOWN_KEYS = ("tag", "performer", "studio", "quality", "recency", "popularity")


@dataclass(frozen=True)
class RecommendationWeights:
    tag: float = 0.30
    performer: float = 0.25
    studio: float = 0.15
    quality: float = 0.10
    recency: float = 0.10
    popularity: float = 0.10

    def __post_init__(self) -> None:
        if any(not isfinite(value) or value < 0 for value in self.__dict__.values()):
            raise ValueError("recommendation weights must be finite and non-negative")


@dataclass(frozen=True)
class UserInterestProfile:
    tags: Mapping[str, float] = field(default_factory=dict)
    performers: Mapping[str, float] = field(default_factory=dict)
    studios: Mapping[str, float] = field(default_factory=dict)
    qualities: Mapping[str, float] = field(default_factory=dict)
    blocked_tags: frozenset[str] = field(default_factory=frozenset)
    blocked_performers: frozenset[str] = field(default_factory=frozenset)


@dataclass(frozen=True)
class RecommendationCandidate:
    tags: frozenset[str] = field(default_factory=frozenset)
    performers: frozenset[str] = field(default_factory=frozenset)
    studio: str | None = None
    quality: str | None = None
    release_date: date | None = None
    popularity: float = 0


@dataclass(frozen=True)
class RecommendationScore:
    score: float
    breakdown: dict[str, float]
    blocked: bool
    blocked_reason: str | None = None


def score_recommendation(
    candidate: RecommendationCandidate,
    profile: UserInterestProfile,
    *,
    weights: RecommendationWeights | None = None,
    now: date | None = None,
) -> RecommendationScore:
    """Return a weighted score and components without touching external state."""

    if candidate.tags & profile.blocked_tags:
        return _blocked("tag")
    if candidate.performers & profile.blocked_performers:
        return _blocked("performer")

    resolved_weights = weights or RecommendationWeights()
    components = {
        "tag": _best_match(candidate.tags, profile.tags) * resolved_weights.tag,
        "performer": _best_match(candidate.performers, profile.performers)
        * resolved_weights.performer,
        "studio": _match(candidate.studio, profile.studios) * resolved_weights.studio,
        "quality": _match(candidate.quality, profile.qualities) * resolved_weights.quality,
        "recency": _recency(candidate.release_date, now) * resolved_weights.recency,
        "popularity": _normalise(candidate.popularity) * resolved_weights.popularity,
    }
    return RecommendationScore(score=sum(components.values()), breakdown=components, blocked=False)


def _blocked(reason: str) -> RecommendationScore:
    return RecommendationScore(
        score=0,
        breakdown=dict.fromkeys(_BREAKDOWN_KEYS, 0),
        blocked=True,
        blocked_reason=reason,
    )


def _best_match(subjects: frozenset[str], preferences: Mapping[str, float]) -> float:
    return max((_match(subject, preferences) for subject in subjects), default=0)


def _match(subject: str | None, preferences: Mapping[str, float]) -> float:
    return _normalise(preferences.get(subject, 0)) if subject is not None else 0


def _recency(release_date: date | None, now: date | None) -> float:
    if release_date is None:
        return 0
    if now is None:
        raise ValueError("now is required when scoring a dated candidate")
    return _normalise(1 - max((now - release_date).days, 0) / 365)


def _normalise(value: float) -> float:
    return min(max(value, 0), 1)
