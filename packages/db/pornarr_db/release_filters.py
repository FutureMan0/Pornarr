"""Shared content-filter evaluation for cached releases."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from pornarr_core.filters import ContentCandidate, FilterDecision, evaluate_filters, resolve_rules
from pornarr_core.filters import FilterAction as CoreFilterAction
from pornarr_core.filters import FilterRule as CoreFilterRule
from pornarr_core.filters import FilterRuleKind as CoreFilterRuleKind
from pornarr_db.models.filters import (
    ContentFilterProfile,
    ContentFilterRule,
    FilterProfileScope,
)
from pornarr_db.models.release import ReleaseCache


async def release_filter_decision(
    session: AsyncSession, user_id: UUID, release: ReleaseCache
) -> FilterDecision:
    """Resolve the global and user filter profiles for one cached release."""

    profiles = await session.scalars(
        select(ContentFilterProfile)
        .options(selectinload(ContentFilterProfile.rules))
        .where(
            or_(
                ContentFilterProfile.scope == FilterProfileScope.GLOBAL,
                ContentFilterProfile.user_id == user_id,
            )
        )
    )
    global_rules: list[CoreFilterRule] = []
    user_rules: list[CoreFilterRule] = []
    for profile in profiles:
        rules = global_rules if profile.scope is FilterProfileScope.GLOBAL else user_rules
        rules.extend(_core_filter_rule(rule) for rule in profile.rules)
    return evaluate_filters(
        ContentCandidate(title=release.title), resolve_rules(global_rules, user_rules)
    )


def _core_filter_rule(rule: ContentFilterRule) -> CoreFilterRule:
    return CoreFilterRule(
        id=str(rule.id),
        kind=CoreFilterRuleKind(rule.kind.value),
        pattern=rule.pattern,
        action=CoreFilterAction(rule.action.value),
        enabled=rule.enabled,
    )
