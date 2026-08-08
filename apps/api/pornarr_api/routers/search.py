"""Authenticated local-library search."""

from __future__ import annotations

from datetime import date
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from pornarr_api.auth import database_session, get_current_user
from pornarr_core.filters import (
    ContentCandidate,
    evaluate_filters,
    resolve_rules,
)
from pornarr_core.filters import FilterAction as CoreFilterAction
from pornarr_core.filters import FilterRule as CoreFilterRule
from pornarr_core.filters import FilterRuleKind as CoreFilterRuleKind
from pornarr_db.media_search import (
    MediaSearch,
    MediaSearchResult,
    MediaSort,
    encode_cursor,
    search_media,
    search_metadata,
)
from pornarr_db.models.filters import (
    ContentFilterProfile,
    ContentFilterRule,
    FilterProfileScope,
)
from pornarr_db.models.user import User

router = APIRouter(prefix="/search", tags=["search"])
CurrentUser = Annotated[User, Depends(get_current_user)]
Session = Annotated[AsyncSession, Depends(database_session)]


class LocalSearchItem(BaseModel):
    id: UUID
    title: str
    studio: str | None
    release_date: date | None
    quality: str | None
    resolution: str | None
    size: int
    duration_seconds: float | None
    performers: list[str]
    tags: list[str]
    relevance: float


class LocalSearchResponse(BaseModel):
    items: list[LocalSearchItem]
    next_cursor: str | None


@router.get("/local", response_model=LocalSearchResponse)
async def local_search(
    user: CurrentUser,
    session: Session,
    q: Annotated[str, Query(min_length=1, max_length=512)],
    quality: str | None = None,
    year: Annotated[int | None, Query(ge=1900, le=9999)] = None,
    studio: str | None = None,
    performer: str | None = None,
    tag: str | None = None,
    minimum_duration_seconds: Annotated[float | None, Query(ge=0)] = None,
    maximum_duration_seconds: Annotated[float | None, Query(ge=0)] = None,
    sort: MediaSort = MediaSort.RELEVANCE,
    cursor: str | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> LocalSearchResponse:
    try:
        search = MediaSearch(
            query=q,
            quality=quality,
            year=year,
            studio=studio,
            performer=performer,
            tag=tag,
            minimum_duration_seconds=minimum_duration_seconds,
            maximum_duration_seconds=maximum_duration_seconds,
            sort=sort,
            cursor=cursor,
            limit=limit + 1,
        )
        results = await search_media(session, search)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error

    page = results[:limit]
    metadata = await search_metadata(session, [result.media.id for result in page])
    rules = await _rules_for_user(session, user.id)
    items = [
        _search_item(result, metadata[result.media.id])
        for result in page
        if _is_visible(result, metadata[result.media.id], rules)
    ]
    next_cursor = _next_cursor(results, limit, sort, q)
    return LocalSearchResponse(items=items, next_cursor=next_cursor)


async def _rules_for_user(session: AsyncSession, user_id: UUID) -> tuple[CoreFilterRule, ...]:
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
        target = global_rules if profile.scope is FilterProfileScope.GLOBAL else user_rules
        target.extend(_core_rule(rule) for rule in profile.rules)
    return resolve_rules(global_rules, user_rules)


def _core_rule(rule: ContentFilterRule) -> CoreFilterRule:
    return CoreFilterRule(
        id=str(rule.id),
        kind=CoreFilterRuleKind(rule.kind.value),
        pattern=rule.pattern,
        action=CoreFilterAction(rule.action.value),
        enabled=rule.enabled,
    )


def _is_visible(result: MediaSearchResult, metadata, rules: tuple[CoreFilterRule, ...]) -> bool:
    candidate = ContentCandidate(
        title=result.media.title,
        description=result.media.description or "",
        tags=metadata.tags,
        performers=metadata.performers,
        metadata_confidence=result.media.confidence or 0.0,
        has_unknown_performer_age=metadata.has_unknown_performer_age,
        file_type=result.media_file.container,
    )
    return evaluate_filters(candidate, rules).action is CoreFilterAction.ALLOW


def _search_item(result: MediaSearchResult, metadata) -> LocalSearchItem:
    return LocalSearchItem(
        id=result.media.id,
        title=result.media.title,
        studio=result.media.studio,
        release_date=result.media.release_date,
        quality=result.media_file.quality,
        resolution=result.media_file.resolution,
        size=result.media_file.size,
        duration_seconds=result.media_file.duration_seconds,
        performers=sorted(metadata.performers),
        tags=sorted(metadata.tags),
        relevance=result.relevance,
    )


def _next_cursor(
    results: list[MediaSearchResult], limit: int, sort: MediaSort, query: str
) -> str | None:
    if len(results) <= limit:
        return None
    result = results[limit - 1]
    values: dict[MediaSort, object] = {
        MediaSort.RELEVANCE: 0.0
        if result.media.normalized_title == query.casefold()
        else 1.0 - result.relevance,
        MediaSort.DATE_ADDED: result.media.created_at,
        MediaSort.TITLE: result.media.normalized_title,
        MediaSort.SIZE: result.media_file.size,
        MediaSort.DURATION: result.media_file.duration_seconds
        if result.media_file.duration_seconds is not None
        else -1,
    }
    return encode_cursor((values[sort], result.media.id))
