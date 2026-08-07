"""Pure exact and conservative fuzzy duplicate detection."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from difflib import SequenceMatcher
from enum import StrEnum

_TITLE_SIMILARITY_THRESHOLD = 0.85
_MAX_DATE_DISTANCE_DAYS = 2
_MAX_DURATION_DIFFERENCE = 0.05


class DuplicateClassification(StrEnum):
    DUPLICATE = "duplicate"
    UPGRADE_CANDIDATE = "upgrade_candidate"
    DISTINCT = "distinct"


class DuplicateSignal(StrEnum):
    OSHASH = "oshash"
    TITLE = "title"
    STUDIO = "studio"
    RELEASE_DATE = "release_date"
    DURATION = "duration"


@dataclass(frozen=True, slots=True)
class MediaCandidate:
    title: str
    studio: str | None
    release_date: date | None
    duration_seconds: float | None
    oshash: str | None = None


@dataclass(frozen=True, slots=True)
class DuplicateDecision:
    classification: DuplicateClassification
    signals: frozenset[DuplicateSignal]


def detect_duplicate(incoming: MediaCandidate, existing: MediaCandidate) -> DuplicateDecision:
    """Classify an exact duplicate, fuzzy upgrade candidate or distinct media."""
    if incoming.oshash and incoming.oshash == existing.oshash:
        return DuplicateDecision(
            DuplicateClassification.DUPLICATE, frozenset({DuplicateSignal.OSHASH})
        )

    signals = frozenset(
        signal
        for signal, matches in (
            (DuplicateSignal.TITLE, _title_matches(incoming.title, existing.title)),
            (DuplicateSignal.STUDIO, _studio_matches(incoming.studio, existing.studio)),
            (
                DuplicateSignal.RELEASE_DATE,
                _date_matches(incoming.release_date, existing.release_date),
            ),
            (
                DuplicateSignal.DURATION,
                _duration_matches(incoming.duration_seconds, existing.duration_seconds),
            ),
        )
        if matches
    )
    fuzzy_signals = {
        DuplicateSignal.TITLE,
        DuplicateSignal.STUDIO,
        DuplicateSignal.RELEASE_DATE,
        DuplicateSignal.DURATION,
    }
    classification = (
        DuplicateClassification.UPGRADE_CANDIDATE
        if signals == fuzzy_signals
        else DuplicateClassification.DISTINCT
    )
    return DuplicateDecision(classification, signals)


def _title_matches(incoming: str, existing: str) -> bool:
    return (
        SequenceMatcher(None, incoming.casefold(), existing.casefold()).ratio()
        > _TITLE_SIMILARITY_THRESHOLD
    )


def _studio_matches(incoming: str | None, existing: str | None) -> bool:
    return (
        bool(incoming and existing) and incoming.strip().casefold() == existing.strip().casefold()
    )


def _date_matches(incoming: date | None, existing: date | None) -> bool:
    return (
        bool(incoming and existing) and abs((incoming - existing).days) <= _MAX_DATE_DISTANCE_DAYS
    )


def _duration_matches(incoming: float | None, existing: float | None) -> bool:
    if not incoming or not existing:
        return False
    return abs(incoming - existing) / max(incoming, existing) <= _MAX_DURATION_DIFFERENCE
