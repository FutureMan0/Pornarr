"""Administrator-managed content-filter rules.

ADR 0017: Pornarr ships no blocklist of its own, so the six rules the migration
seeds are the whole of the instance's filtering and they all ship off and empty.
This is where they are turned on. ADR 0018 puts the rules in a profile at global
scope; the per-user profiles it also describes are read by evaluation, not
configured here, so only the global profile has a route.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from pornarr_api.auth import database_session, require_role
from pornarr_db.audit import write_audit
from pornarr_db.models.filters import (
    ContentFilterProfile,
    ContentFilterRule,
    FilterAction,
    FilterProfileScope,
    FilterRuleKind,
)
from pornarr_db.models.user import User, UserRole
from pornarr_shared.errors import PornarrError

router = APIRouter(prefix="/admin/filters", tags=["admin"])
Admin = Annotated[User, Depends(require_role(UserRole.ADMIN))]
Session = Annotated[AsyncSession, Depends(database_session)]

# The kinds that match on what the operator typed. The remaining three answer a
# property of the candidate itself, so a pattern on one of them is a value that
# would never be read.
_PATTERN_KINDS = frozenset({FilterRuleKind.TERM, FilterRuleKind.TAG, FilterRuleKind.PERFORMER})


class FilterConfigurationError(PornarrError):
    code = "FILTER_CONFIGURATION_INVALID"
    status = 422


class FilterRuleWrite(BaseModel):
    kind: FilterRuleKind
    pattern: Annotated[str, Field(max_length=512)] = ""
    action: FilterAction = FilterAction.REJECT
    enabled: bool = False

    @field_validator("pattern")
    @classmethod
    def strip_pattern(cls, value: str) -> str:
        return value.strip()


class FilterProfileWrite(BaseModel):
    rules: Annotated[list[FilterRuleWrite], Field(min_length=1, max_length=len(FilterRuleKind))]

    @field_validator("rules")
    @classmethod
    def require_one_rule_per_kind(cls, values: list[FilterRuleWrite]) -> list[FilterRuleWrite]:
        kinds = [rule.kind for rule in values]
        if len(set(kinds)) != len(kinds):
            raise ValueError("rules must not name the same kind twice")
        return values


class FilterRuleResponse(BaseModel):
    id: UUID
    kind: FilterRuleKind
    pattern: str
    action: FilterAction
    enabled: bool


class FilterProfileResponse(BaseModel):
    id: UUID
    scope: FilterProfileScope
    rules: list[FilterRuleResponse]


@router.get("/profile", response_model=FilterProfileResponse)
async def read_filter_profile(_: Admin, session: Session) -> FilterProfileResponse:
    return _profile_response(await _global_profile(session))


@router.put("/profile", response_model=FilterProfileResponse)
async def update_filter_profile(
    payload: FilterProfileWrite, user: Admin, session: Session
) -> FilterProfileResponse:
    profile = await _global_profile(session)
    by_kind = {rule.kind: rule for rule in profile.rules}
    missing = [write.kind for write in payload.rules if write.kind not in by_kind]
    if missing:
        raise FilterConfigurationError(
            "The profile has no rule of that kind.", reason="unknown_kind"
        )
    for write in payload.rules:
        _validate(write)
        rule = by_kind[write.kind]
        rule.pattern = write.pattern
        rule.action = write.action
        rule.enabled = write.enabled
    await session.flush()
    write_audit(
        session,
        actor_id=user.id,
        action="filter_profile.updated",
        target=str(profile.id),
        # Which rules are live is the whole of the decision, and an audit entry
        # that only said "updated" would not let anyone reconstruct it.
        context={
            "enabled_kinds": sorted(rule.kind.value for rule in profile.rules if rule.enabled)
        },
    )
    return _profile_response(profile)


def _validate(write: FilterRuleWrite) -> None:
    if write.kind in _PATTERN_KINDS:
        if write.enabled and not write.pattern:
            raise FilterConfigurationError(
                "An enabled rule of this kind needs something to match on.",
                reason="pattern_required",
                kind=write.kind.value,
            )
        return
    if write.kind is FilterRuleKind.MINIMUM_CONFIDENCE:
        if not write.enabled:
            return
        try:
            threshold = float(write.pattern)
        except ValueError:
            raise FilterConfigurationError(
                "The minimum confidence must be a number between 0 and 1.",
                reason="confidence_invalid",
                kind=write.kind.value,
            ) from None
        if not 0 <= threshold <= 1:
            raise FilterConfigurationError(
                "The minimum confidence must be a number between 0 and 1.",
                reason="confidence_invalid",
                kind=write.kind.value,
            )
        return
    if write.pattern:
        raise FilterConfigurationError(
            "This rule answers a property of the file and takes no pattern.",
            reason="pattern_not_allowed",
            kind=write.kind.value,
        )


async def _global_profile(session: AsyncSession) -> ContentFilterProfile:
    profile = await session.scalar(
        select(ContentFilterProfile)
        .options(selectinload(ContentFilterProfile.rules))
        .where(ContentFilterProfile.scope == FilterProfileScope.GLOBAL)
    )
    if profile is None:
        raise HTTPException(status_code=404)
    return profile


def _profile_response(profile: ContentFilterProfile) -> FilterProfileResponse:
    return FilterProfileResponse(
        id=profile.id,
        scope=profile.scope,
        rules=[_rule_response(rule) for rule in _ordered(profile.rules)],
    )


def _ordered(rules: list[ContentFilterRule]) -> list[ContentFilterRule]:
    """Declaration order, so the screen that renders them never reshuffles."""
    order = {kind: index for index, kind in enumerate(FilterRuleKind)}
    return sorted(rules, key=lambda rule: order[rule.kind])


def _rule_response(rule: ContentFilterRule) -> FilterRuleResponse:
    return FilterRuleResponse(
        id=rule.id,
        kind=rule.kind,
        pattern=rule.pattern,
        action=rule.action,
        enabled=rule.enabled,
    )
