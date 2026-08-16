"""Pure, explainable score for automatic-download candidates."""

from __future__ import annotations

from dataclasses import dataclass, fields

_PENALTIES = frozenset({"size", "duplicate_risk", "expected_download_time"})


@dataclass(frozen=True, slots=True)
class AutomationScoreInput:
    """Normalized 0-1 signals collected by the worker."""

    recommendation_score: float
    metadata_quality: float
    release_quality: float
    indexer_reliability: float
    recency: float
    size: float
    duplicate_risk: float
    expected_download_time: float


@dataclass(frozen=True, slots=True)
class AutomationWeights:
    """Runtime-tunable factors for the automatic-download formula."""

    recommendation_score: float = 0.30
    metadata_quality: float = 0.15
    release_quality: float = 0.15
    indexer_reliability: float = 0.10
    recency: float = 0.10
    size: float = 0.05
    duplicate_risk: float = 0.10
    expected_download_time: float = 0.05


DEFAULT_AUTOMATION_WEIGHTS = AutomationWeights()


@dataclass(frozen=True, slots=True)
class AutomationScore:
    score: float
    breakdown: dict[str, float]


def score_auto_download(
    candidate: AutomationScoreInput, *, weights: AutomationWeights = DEFAULT_AUTOMATION_WEIGHTS
) -> AutomationScore:
    """Return a weighted score and every positive or negative contribution."""
    breakdown: dict[str, float] = {}
    for signal in fields(candidate):
        value = getattr(candidate, signal.name)
        if not 0 <= value <= 1:
            raise ValueError(f"{signal.name} must be normalized between 0 and 1")
        contribution = getattr(weights, signal.name) * value
        breakdown[signal.name] = -contribution if signal.name in _PENALTIES else contribution
    return AutomationScore(score=sum(breakdown.values()), breakdown=breakdown)
