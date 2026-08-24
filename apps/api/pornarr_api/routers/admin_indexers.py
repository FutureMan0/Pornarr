"""Administrator-only indexer configuration and connection diagnostics."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field, SecretStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from pornarr_api.auth import database_session, require_role
from pornarr_db.audit import write_audit
from pornarr_db.models.indexer import Indexer, IndexerStats
from pornarr_db.models.user import User, UserRole
from pornarr_integrations.health import CircuitBreaker, failure_for
from pornarr_integrations.indexers import IndexerAdapter
from pornarr_shared.errors import PornarrError

router = APIRouter(prefix="/admin/indexers", tags=["admin"])
Admin = Annotated[User, Depends(require_role(UserRole.ADMIN))]
Session = Annotated[AsyncSession, Depends(database_session)]


class IndexerConnectionError(PornarrError):
    code = "INDEXER_CONNECTION_FAILED"
    status = 422


class IndexerWrite(BaseModel):
    name: Annotated[str, Field(min_length=1, max_length=128)]
    protocol: Annotated[str, Field(min_length=1, max_length=32)]
    implementation: Annotated[str, Field(min_length=1, max_length=64)]
    base_url: Annotated[str, Field(min_length=1, max_length=512)]
    api_key: SecretStr
    priority: int = 0
    enabled: bool = True
    # None means "use the model's default" rather than "clear the selection".
    search_categories: list[str] | None = None


class IndexerUpdate(IndexerWrite):
    # The key is write-only: it is never returned, so an edit screen has nothing
    # to send back for it. None therefore means "keep the stored key" rather
    # than "clear it", and an indexer can never end up with an empty one.
    api_key: SecretStr | None = None


class IndexerSearchCategoriesWrite(BaseModel):
    search_categories: list[str]


class IndexerStatsResponse(BaseModel):
    queries: int
    failures: int
    average_latency_ms: float | None
    grabs: int


class IndexerResponse(BaseModel):
    id: UUID
    name: str
    protocol: str
    implementation: str
    base_url: str
    categories: list[dict[str, str]]
    search_categories: list[str]
    priority: int
    enabled: bool
    health: str
    health_reason: str | None
    last_error: str | None
    last_tested_at: datetime | None
    stats: IndexerStatsResponse


def indexer_response(indexer: Indexer, stats: IndexerStats) -> IndexerResponse:
    return IndexerResponse(
        id=indexer.id,
        name=indexer.name,
        protocol=indexer.protocol,
        implementation=indexer.implementation,
        base_url=indexer.base_url,
        categories=indexer.categories,
        search_categories=indexer.search_categories,
        priority=indexer.priority,
        enabled=indexer.enabled,
        health=indexer.health,
        health_reason=indexer.health_reason,
        last_error=indexer.last_error,
        last_tested_at=indexer.last_tested_at,
        stats=IndexerStatsResponse(
            queries=stats.queries,
            failures=stats.failures,
            average_latency_ms=stats.average_latency_ms,
            grabs=stats.grabs,
        ),
    )


async def indexer_or_404(session: AsyncSession, indexer_id: UUID) -> Indexer:
    indexer = await session.get(Indexer, indexer_id)
    if indexer is None:
        raise HTTPException(status_code=404)
    return indexer


async def stats_for(session: AsyncSession, indexer_id: UUID) -> IndexerStats:
    stats = await session.get(IndexerStats, indexer_id)
    if stats is None:
        raise RuntimeError("Indexer statistics are missing")
    return stats


def adapter_for(request: Request, indexer: Indexer) -> IndexerAdapter:
    adapters: dict[str, IndexerAdapter] = getattr(request.app.state, "indexer_adapters", {})
    adapter = adapters.get(indexer.implementation)
    if adapter is None:
        raise IndexerConnectionError(
            f"No adapter is registered for {indexer.implementation!r}.",
            reason="adapter is not installed",
        )
    return adapter


def safe_reason(error: Exception, api_key: str) -> str:
    return str(error).replace(api_key, "[redacted]") or type(error).__name__


@router.get("", response_model=list[IndexerResponse])
async def list_indexers(_: Admin, session: Session) -> list[IndexerResponse]:
    indexers = list(await session.scalars(select(Indexer).order_by(Indexer.priority, Indexer.name)))
    stats = {
        item.indexer_id: item
        for item in await session.scalars(
            select(IndexerStats).where(IndexerStats.indexer_id.in_([i.id for i in indexers]))
        )
    }
    return [indexer_response(indexer, stats[indexer.id]) for indexer in indexers]


@router.post("", response_model=IndexerResponse, status_code=201)
async def create_indexer(payload: IndexerWrite, _: Admin, session: Session) -> IndexerResponse:
    indexer = Indexer(
        name=payload.name,
        protocol=payload.protocol,
        implementation=payload.implementation,
        base_url=payload.base_url.rstrip("/"),
        api_key=payload.api_key.get_secret_value(),
        priority=payload.priority,
        enabled=payload.enabled,
    )
    if payload.search_categories is not None:
        indexer.search_categories = payload.search_categories
    session.add(indexer)
    await session.flush()
    stats = IndexerStats(indexer_id=indexer.id)
    session.add(stats)
    await session.flush()
    return indexer_response(indexer, stats)


@router.put("/{indexer_id}", response_model=IndexerResponse)
async def update_indexer(
    indexer_id: UUID, payload: IndexerUpdate, user: Admin, session: Session
) -> IndexerResponse:
    """Change a configured indexer in place.

    ADR 0002 L9: indexers are managed through the administration UI. Without this
    route the only edit was delete-and-recreate, which discards the indexer's
    health, its statistics and — through `ON DELETE CASCADE` on
    `release_cache.indexer_id` — every release cached from it.
    """

    indexer = await indexer_or_404(session, indexer_id)
    indexer.name = payload.name
    indexer.protocol = payload.protocol
    indexer.implementation = payload.implementation
    indexer.base_url = payload.base_url.rstrip("/")
    indexer.priority = payload.priority
    indexer.enabled = payload.enabled
    if payload.api_key is not None:
        indexer.api_key = payload.api_key.get_secret_value()
    if payload.search_categories is not None:
        indexer.search_categories = payload.search_categories
    await session.flush()
    write_audit(session, actor_id=user.id, action="indexer.updated", target=str(indexer.id))
    return indexer_response(indexer, await stats_for(session, indexer.id))


@router.post("/{indexer_id}/test", response_model=IndexerResponse)
async def test_indexer(
    indexer_id: UUID, request: Request, _: Admin, session: Session
) -> IndexerResponse:
    indexer = await indexer_or_404(session, indexer_id)
    try:
        categories = await adapter_for(request, indexer).test_connection(
            base_url=indexer.base_url, api_key=indexer.api_key
        )
    except IndexerConnectionError:
        raise
    except Exception as error:
        reason = safe_reason(error, indexer.api_key)
        indexer.health = "unhealthy"
        indexer.health_reason = failure_for(error).value
        indexer.last_error = reason
        indexer.last_tested_at = datetime.now(UTC)
        await session.commit()
        raise IndexerConnectionError("The indexer connection failed.", reason=reason) from error
    indexer.categories = [{"id": category.id, "name": category.name} for category in categories]
    indexer.health = "healthy"
    indexer.health_reason = None
    indexer.last_error = None
    indexer.last_tested_at = datetime.now(UTC)
    return indexer_response(indexer, await stats_for(session, indexer.id))


@router.put("/{indexer_id}/search-categories", response_model=IndexerResponse)
async def update_indexer_search_categories(
    indexer_id: UUID, payload: IndexerSearchCategoriesWrite, _: Admin, session: Session
) -> IndexerResponse:
    """Change which categories this indexer's search/rss requests are restricted to."""

    indexer = await indexer_or_404(session, indexer_id)
    indexer.search_categories = payload.search_categories
    return indexer_response(indexer, await stats_for(session, indexer.id))


@router.post("/{indexer_id}/reset", response_model=IndexerResponse)
async def reset_indexer(
    indexer_id: UUID, request: Request, _: Admin, session: Session
) -> IndexerResponse:
    indexer = await indexer_or_404(session, indexer_id)
    indexer.health = (await CircuitBreaker(request.app.state.redis).reset(str(indexer.id))).value
    indexer.health_reason = None
    indexer.last_error = None
    return indexer_response(indexer, await stats_for(session, indexer.id))


@router.delete("/{indexer_id}", status_code=204)
async def delete_indexer(indexer_id: UUID, _: Admin, session: Session) -> None:
    await session.delete(await indexer_or_404(session, indexer_id))
