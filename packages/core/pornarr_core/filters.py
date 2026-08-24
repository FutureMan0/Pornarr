"""Pure, deterministic content-filter evaluation."""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum

_SEPARATORS = re.compile(r"[^0-9a-z]+")


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
    # Every rule that matched, not only the one whose action won.
    # docs/pipelines/import.md L31 asks for "every firing rule" in the audit log,
    # and a decision that reported one of them could not answer that: two rules
    # firing and one being written down is a record of the outcome, not of what
    # the instance actually decided against.
    matched: tuple[FilterRule, ...] = ()


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
    return FilterDecision(action=rule.action, rule=rule, matched=matching_rules)


def _folded(value: str) -> str:
    """Case and separators reduced to one spelling, and nothing else."""

    return _SEPARATORS.sub(" ", value.casefold()).strip()


def _matches(rule: FilterRule, candidate: ContentCandidate) -> bool:
    if not rule.enabled:
        return False

    pattern = rule.pattern.casefold()
    if rule.kind is FilterRuleKind.TERM:
        # Separators folded on both sides. An operator writes the release's own
        # spelling - `desi.bang`, `true_amateurs`, `gauntlet-hold` - while the
        # title this is compared against has already been through
        # `split_release_name`, which turns every separator into a space.
        # Compared literally, a term carrying any separator matched nothing at
        # all, and a filter that silently never fires is worse than no filter.
        # Only separators are folded: two different words stay two different
        # words.
        term = _folded(pattern)
        return bool(term) and (
            term in _folded(candidate.title) or term in _folded(candidate.description)
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
