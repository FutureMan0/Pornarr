"""Pure, deterministic content-filter evaluation."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum


class FilterRuleKind(StrEnum):
    TERM = "term"
    TAG = "tag"
    PERFORMER = "performer"
    MINIMUM_CONFIDENCE = "minimum_confidence"
    UNKNOWN_PERFORMER_AGE = "unknown_performer_age"
    UNKNOWN_FILE_TYPE = "unknown_file_type"


class FilterAction(StrEnum):
    ALLOW = "allow"
    QUARANTINE = "quarantine"
    REJECT = "reject"


@dataclass(frozen=True, slots=True)
class ContentCandidate:
    """The filter-relevant fields produced by search, grab and import."""

    title: str
    description: str = ""
    tags: frozenset[str] = frozenset()
    performers: frozenset[str] = frozenset()
    metadata_confidence: float = 0.0
    has_unknown_performer_age: bool = False
    file_type: str | None = None


@dataclass(frozen=True, slots=True)
class FilterRule:
    """The ORM-independent representation of an enabled or disabled stored rule."""

    id: str
    kind: FilterRuleKind
    pattern: str
    action: FilterAction
    enabled: bool = True


@dataclass(frozen=True, slots=True)
class FilterDecision:
    action: FilterAction
    rule: FilterRule | None


_ACTION_PRIORITY = {
    FilterAction.ALLOW: 0,
    FilterAction.QUARANTINE: 1,
    FilterAction.REJECT: 2,
}


def resolve_rules(
    global_rules: Iterable[FilterRule], user_rules: Iterable[FilterRule]
) -> tuple[FilterRule, ...]:
    """Combine active global and user rules, preserving global tie precedence."""
    return tuple(rule for rule in (*global_rules, *user_rules) if rule.enabled)


def evaluate_filters(candidate: ContentCandidate, rules: Iterable[FilterRule]) -> FilterDecision:
    """Return the highest-precedence matching action and its producing rule."""
    matching_rules = tuple(rule for rule in rules if _matches(rule, candidate))
    if not matching_rules:
        return FilterDecision(action=FilterAction.ALLOW, rule=None)

    # `max` keeps the first item on an equal key, so the order resolved above
    # deterministically makes an equally strict global rule win over a user rule.
    rule = max(matching_rules, key=lambda candidate_rule: _ACTION_PRIORITY[candidate_rule.action])
    return FilterDecision(action=rule.action, rule=rule)


def _matches(rule: FilterRule, candidate: ContentCandidate) -> bool:
    if not rule.enabled:
        return False

    pattern = rule.pattern.casefold()
    if rule.kind is FilterRuleKind.TERM:
        return bool(pattern) and (
            pattern in candidate.title.casefold() or pattern in candidate.description.casefold()
        )
    if rule.kind is FilterRuleKind.TAG:
        return bool(pattern) and any(pattern == tag.casefold() for tag in candidate.tags)
    if rule.kind is FilterRuleKind.PERFORMER:
        return bool(pattern) and any(
            pattern == performer.casefold() for performer in candidate.performers
        )
    if rule.kind is FilterRuleKind.MINIMUM_CONFIDENCE:
        try:
            threshold = float(rule.pattern)
        except ValueError:
            return False
        return candidate.metadata_confidence < threshold
    if rule.kind is FilterRuleKind.UNKNOWN_PERFORMER_AGE:
        return candidate.has_unknown_performer_age
    return candidate.file_type is None
