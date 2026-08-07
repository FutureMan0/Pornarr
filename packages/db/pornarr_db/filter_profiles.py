"""Business rules for changing content-filter profiles."""

from __future__ import annotations

from pornarr_db.models.filters import (
    ContentFilterProfile,
    ContentFilterRule,
    FilterAction,
    FilterProfileScope,
    FilterRuleKind,
)


def add_filter_rule(
    profile: ContentFilterProfile,
    *,
    kind: FilterRuleKind,
    pattern: str,
    action: FilterAction,
    enabled: bool = False,
) -> ContentFilterRule:
    """Add a rule without allowing a user profile to loosen the global policy."""
    if profile.scope is FilterProfileScope.USER and action is FilterAction.ALLOW:
        raise ValueError("User profiles may only tighten the global filter profile.")

    rule = ContentFilterRule(kind=kind, pattern=pattern, action=action, enabled=enabled)
    profile.rules.append(rule)
    return rule
