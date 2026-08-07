"""Content-filter evaluation."""

from __future__ import annotations

import pytest

from pornarr_core.filters import (
    ContentCandidate,
    FilterAction,
    FilterRule,
    FilterRuleKind,
    evaluate_filters,
    resolve_rules,
)


@pytest.mark.parametrize("action", list(FilterAction))
@pytest.mark.parametrize(
    ("kind", "pattern", "candidate"),
    [
        (
            FilterRuleKind.TERM,
            "example",
            ContentCandidate(title="An Example Title"),
        ),
        (
            FilterRuleKind.TAG,
            "example",
            ContentCandidate(title="title", tags=frozenset({"Example"})),
        ),
        (
            FilterRuleKind.PERFORMER,
            "example",
            ContentCandidate(title="title", performers=frozenset({"Example"})),
        ),
        (
            FilterRuleKind.MINIMUM_CONFIDENCE,
            "0.7",
            ContentCandidate(title="title", metadata_confidence=0.6),
        ),
        (
            FilterRuleKind.UNKNOWN_PERFORMER_AGE,
            "",
            ContentCandidate(title="title", has_unknown_performer_age=True),
        ),
        (
            FilterRuleKind.UNKNOWN_FILE_TYPE,
            "",
            ContentCandidate(title="title", file_type=None),
        ),
    ],
)
def test_each_rule_kind_returns_its_action_and_rule(
    action: FilterAction,
    kind: FilterRuleKind,
    pattern: str,
    candidate: ContentCandidate,
) -> None:
    rule = FilterRule(id=f"{kind}-{action}", kind=kind, pattern=pattern, action=action)

    decision = evaluate_filters(candidate, [rule])

    assert decision.action is action
    assert decision.rule is rule


def test_reject_wins_over_quarantine_and_allow() -> None:
    allow = FilterRule(
        id="allow", kind=FilterRuleKind.TERM, pattern="example", action=FilterAction.ALLOW
    )
    quarantine = FilterRule(
        id="quarantine", kind=FilterRuleKind.TAG, pattern="example", action=FilterAction.QUARANTINE
    )
    reject = FilterRule(
        id="reject", kind=FilterRuleKind.PERFORMER, pattern="example", action=FilterAction.REJECT
    )
    candidate = ContentCandidate(
        title="Example", tags=frozenset({"example"}), performers=frozenset({"EXAMPLE"})
    )

    decision = evaluate_filters(candidate, [allow, quarantine, reject])

    assert decision.action is FilterAction.REJECT
    assert decision.rule is reject


def test_resolved_rules_ignore_disabled_entries_and_keep_global_tie_breaking() -> None:
    disabled = FilterRule(
        id="disabled",
        kind=FilterRuleKind.TERM,
        pattern="example",
        action=FilterAction.REJECT,
        enabled=False,
    )
    global_rule = FilterRule(
        id="global", kind=FilterRuleKind.TERM, pattern="example", action=FilterAction.QUARANTINE
    )
    user_rule = FilterRule(
        id="user", kind=FilterRuleKind.TERM, pattern="example", action=FilterAction.QUARANTINE
    )

    resolved = resolve_rules([disabled, global_rule], [user_rule])
    decision = evaluate_filters(ContentCandidate(title="Example"), resolved)

    assert resolved == (global_rule, user_rule)
    assert decision.rule is global_rule


def test_a_nonmatching_or_invalid_rule_leaves_the_candidate_allowed() -> None:
    rules = [
        FilterRule(
            id="term", kind=FilterRuleKind.TERM, pattern="different", action=FilterAction.REJECT
        ),
        FilterRule(
            id="confidence",
            kind=FilterRuleKind.MINIMUM_CONFIDENCE,
            pattern="not-a-number",
            action=FilterAction.REJECT,
        ),
    ]

    decision = evaluate_filters(ContentCandidate(title="Example", description="Description"), rules)

    assert decision.action is FilterAction.ALLOW
    assert decision.rule is None


def test_term_rules_match_the_description_case_insensitively() -> None:
    rule = FilterRule(
        id="description",
        kind=FilterRuleKind.TERM,
        pattern="EXAMPLE",
        action=FilterAction.QUARANTINE,
    )

    decision = evaluate_filters(ContentCandidate(title="Title", description="an example"), [rule])

    assert decision.rule is rule
