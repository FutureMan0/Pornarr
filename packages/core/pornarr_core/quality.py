"""Pure quality-profile decisions for grabbing and upgrading releases."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class QualityVerdict(StrEnum):
    GRAB = "grab"
    UPGRADE = "upgrade"
    REJECT = "reject"


class QualityReason(StrEnum):
    NOT_IN_PROFILE = "not_in_profile"
    BELOW_MINIMUM_SCORE = "below_minimum_score"
    NO_EXISTING_FILE = "no_existing_file"
    NOT_AN_UPGRADE = "not_an_upgrade"
    CUTOFF_MET = "cutoff_met"
    UPGRADE_AVAILABLE = "upgrade_available"


@dataclass(frozen=True, slots=True)
class QualityProfile:
    allowed_qualities: frozenset[str]
    cutoff_quality_rank: int
    minimum_custom_format_score: int = 0


@dataclass(frozen=True, slots=True)
class ReleaseCandidate:
    quality: str
    quality_rank: int
    custom_format_scores: tuple[int, ...] = ()

    @property
    def score(self) -> int:
        return self.quality_rank + sum(self.custom_format_scores)


@dataclass(frozen=True, slots=True)
class ExistingFile:
    quality_rank: int
    custom_format_score: int = 0

    @property
    def score(self) -> int:
        return self.quality_rank + self.custom_format_score


@dataclass(frozen=True, slots=True)
class QualityDecision:
    verdict: QualityVerdict
    reason: QualityReason
    score: int


def decide_quality(
    candidate: ReleaseCandidate,
    profile: QualityProfile,
    *,
    existing: ExistingFile | None,
) -> QualityDecision:
    """Return the grab, upgrade or rejection verdict with its explicit reason."""
    score = candidate.score
    if candidate.quality not in profile.allowed_qualities:
        return QualityDecision(QualityVerdict.REJECT, QualityReason.NOT_IN_PROFILE, score)
    if score < profile.minimum_custom_format_score:
        return QualityDecision(QualityVerdict.REJECT, QualityReason.BELOW_MINIMUM_SCORE, score)
    if existing is None:
        return QualityDecision(QualityVerdict.GRAB, QualityReason.NO_EXISTING_FILE, score)
    if score <= existing.score:
        return QualityDecision(QualityVerdict.REJECT, QualityReason.NOT_AN_UPGRADE, score)
    if existing.quality_rank >= profile.cutoff_quality_rank:
        return QualityDecision(QualityVerdict.REJECT, QualityReason.CUTOFF_MET, score)
    return QualityDecision(QualityVerdict.UPGRADE, QualityReason.UPGRADE_AVAILABLE, score)
