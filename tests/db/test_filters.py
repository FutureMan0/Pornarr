"""Content-filter profile invariants."""

from __future__ import annotations

from uuid import uuid4

import pytest

from pornarr_db.filter_profiles import add_filter_rule
from pornarr_db.models.filters import (
    ContentFilterProfile,
    FilterAction,
    FilterProfileScope,
    FilterRuleKind,
)


def test_user_profile_cannot_add_an_allow_rule() -> None:
    profile = ContentFilterProfile(scope=FilterProfileScope.USER, user_id=uuid4())

    with pytest.raises(ValueError, match="only tighten"):
        add_filter_rule(
            profile,
            kind=FilterRuleKind.TERM,
            pattern="example",
            action=FilterAction.ALLOW,
        )


@pytest.mark.parametrize("action", [FilterAction.QUARANTINE, FilterAction.REJECT])
def test_user_profile_can_add_restrictive_rules(action: FilterAction) -> None:
    profile = ContentFilterProfile(scope=FilterProfileScope.USER, user_id=uuid4())

    rule = add_filter_rule(
        profile,
        kind=FilterRuleKind.PERFORMER,
        pattern="example",
        action=action,
    )

    assert profile.rules == [rule]
    assert rule.enabled is False


def test_global_profile_can_add_allow_rule() -> None:
    profile = ContentFilterProfile(scope=FilterProfileScope.GLOBAL)

    rule = add_filter_rule(
        profile,
        kind=FilterRuleKind.TAG,
        pattern="example",
        action=FilterAction.ALLOW,
        enabled=True,
    )

    assert rule.action is FilterAction.ALLOW
    assert rule.enabled is True
