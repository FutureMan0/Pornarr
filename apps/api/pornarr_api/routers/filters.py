"""Content-filter profiles and explainable dry runs."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from pornarr_api.auth import database_session, get_current_user, require_role
from pornarr_core.filters import ContentCandidate, FilterAction as CoreAction, FilterRule, FilterRuleKind, evaluate_filters, resolve_rules
from pornarr_db.filter_profiles import add_filter_rule
from pornarr_db.models.filters import ContentFilterProfile, ContentFilterRule, FilterAction, FilterProfileScope, FilterRuleKind as DbRuleKind
from pornarr_db.models.user import User, UserRole

router = APIRouter(prefix="/filters", tags=["filters"])
CurrentUser = Annotated[User, Depends(get_current_user)]
Admin = Annotated[User, Depends(require_role(UserRole.ADMIN))]
Session = Annotated[AsyncSession, Depends(database_session)]


class RuleWrite(BaseModel):
    kind: DbRuleKind
    pattern: str = Field(max_length=512)
    action: FilterAction
    enabled: bool = True


class RuleResponse(RuleWrite):
    id: UUID
    global_rule: bool


class FilterProfilesResponse(BaseModel):
    global_rules: list[RuleResponse]
    personal_rules: list[RuleResponse]


class DryRunWrite(BaseModel):
    title: str = Field(min_length=1, max_length=512)


class DryRunResponse(BaseModel):
    action: FilterAction
    rule_id: UUID | None


async def profiles(session: AsyncSession, user_id: UUID) -> tuple[ContentFilterProfile, ContentFilterProfile]:
    rows = list(await session.scalars(select(ContentFilterProfile).options(selectinload(ContentFilterProfile.rules)).where((ContentFilterProfile.scope == FilterProfileScope.GLOBAL) | (ContentFilterProfile.user_id == user_id))))
    global_profile = next((row for row in rows if row.scope is FilterProfileScope.GLOBAL), None)
    if global_profile is None:
        global_profile = ContentFilterProfile(scope=FilterProfileScope.GLOBAL)
        session.add(global_profile)
        await session.flush()
    personal = next((row for row in rows if row.scope is FilterProfileScope.USER), None)
    if personal is None:
        personal = ContentFilterProfile(scope=FilterProfileScope.USER, user_id=user_id)
        session.add(personal)
        await session.flush()
    return global_profile, personal


def response(rule: ContentFilterRule, global_rule: bool) -> RuleResponse:
    return RuleResponse(id=rule.id, kind=rule.kind, pattern=rule.pattern, action=rule.action, enabled=rule.enabled, global_rule=global_rule)


@router.get("", response_model=FilterProfilesResponse)
async def read_filters(user: CurrentUser, session: Session) -> FilterProfilesResponse:
    global_profile, personal = await profiles(session, user.id)
    return FilterProfilesResponse(global_rules=[response(rule, True) for rule in global_profile.rules], personal_rules=[response(rule, False) for rule in personal.rules])


@router.post("/rules", response_model=RuleResponse, status_code=201)
async def add_personal_rule(payload: RuleWrite, user: CurrentUser, session: Session) -> RuleResponse:
    _, personal = await profiles(session, user.id)
    try:
        rule = add_filter_rule(personal, kind=payload.kind, pattern=payload.pattern, action=payload.action, enabled=payload.enabled)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    await session.flush()
    return response(rule, False)


@router.post("/global/rules", response_model=RuleResponse, status_code=201)
async def add_global_rule(payload: RuleWrite, user: Admin, session: Session) -> RuleResponse:
    global_profile, _ = await profiles(session, user.id)
    rule = add_filter_rule(global_profile, kind=payload.kind, pattern=payload.pattern, action=payload.action, enabled=payload.enabled)
    await session.flush()
    return response(rule, True)


@router.patch("/rules/{rule_id}", response_model=RuleResponse)
async def update_personal_rule(rule_id: UUID, payload: RuleWrite, user: CurrentUser, session: Session) -> RuleResponse:
    rule = await session.get(ContentFilterRule, rule_id)
    if rule is None or rule.profile.user_id != user.id:
        raise HTTPException(status_code=404)
    if payload.action is FilterAction.ALLOW:
        raise HTTPException(status_code=422)
    rule.kind, rule.pattern, rule.action, rule.enabled = payload.kind, payload.pattern, payload.action, payload.enabled
    return response(rule, False)


@router.patch("/global/rules/{rule_id}", response_model=RuleResponse)
async def update_global_rule(
    rule_id: UUID, payload: RuleWrite, _: Admin, session: Session
) -> RuleResponse:
    rule = await session.get(ContentFilterRule, rule_id)
    if rule is None or rule.profile.scope is not FilterProfileScope.GLOBAL:
        raise HTTPException(status_code=404)
    rule.kind, rule.pattern, rule.action, rule.enabled = payload.kind, payload.pattern, payload.action, payload.enabled
    return response(rule, True)


@router.post("/dry-run", response_model=DryRunResponse)
async def dry_run(payload: DryRunWrite, user: CurrentUser, session: Session) -> DryRunResponse:
    global_profile, personal = await profiles(session, user.id)
    to_core = lambda rule: FilterRule(str(rule.id), FilterRuleKind(rule.kind.value), rule.pattern, CoreAction(rule.action.value), rule.enabled)
    decision = evaluate_filters(ContentCandidate(title=payload.title), resolve_rules(map(to_core, global_profile.rules), map(to_core, personal.rules)))
    return DryRunResponse(action=FilterAction(decision.action.value), rule_id=UUID(decision.rule.id) if decision.rule else None)
