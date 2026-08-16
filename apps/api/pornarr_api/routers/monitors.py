"""Authenticated monitor management for automatic release discovery."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response
from fastapi import Request as HttpRequest
from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from pornarr_api.auth import database_session, get_current_user
from pornarr_api.errors import ErrorResponse
from pornarr_db.models.entities import Performer, Studio
from pornarr_db.models.monitor import Monitor, MonitorKind
from pornarr_db.models.quality import QualityProfile
from pornarr_db.models.user import User
from pornarr_db.release_cache import normalize_release_title
from pornarr_shared.errors import PornarrError
from pornarr_shared.jobs import BACKLOG_SEARCH_JOB_NAME, INDEXER_QUEUE, enqueue_once

router = APIRouter(prefix="/monitors", tags=["monitors"])
CurrentUser = Annotated[User, Depends(get_current_user)]
Session = Annotated[AsyncSession, Depends(database_session)]


class MonitorQualityProfileError(PornarrError):
    code = "MONITOR_QUALITY_PROFILE_REQUIRED"
    status = 409


class MonitorDuplicateError(PornarrError):
    code = "MONITOR_ALREADY_EXISTS"
    status = 409


class MonitorCreate(BaseModel):
    kind: MonitorKind
    performer_id: UUID | None = None
    studio_id: UUID | None = None
    query: Annotated[str | None, Field(max_length=512)] = None
    quality_profile_id: UUID | None = None
    enabled: bool = True
    minimum_score: Annotated[float, Field(ge=0)] = 0

    @field_validator("query")
    @classmethod
    def strip_query(cls, value: str | None) -> str | None:
        return value.strip() if value is not None else None

    @model_validator(mode="after")
    def has_exactly_one_target(self) -> MonitorCreate:
        expected = {
            MonitorKind.PERFORMER: self.performer_id is not None
            and self.studio_id is None
            and self.query is None,
            MonitorKind.STUDIO: self.performer_id is None
            and self.studio_id is not None
            and self.query is None,
            MonitorKind.QUERY: self.performer_id is None
            and self.studio_id is None
            and self.query is not None
            and bool(self.query),
        }
        if not expected[self.kind]:
            raise ValueError("kind must have exactly one matching target")
        return self


class MonitorUpdate(BaseModel):
    query: Annotated[str | None, Field(max_length=512)] = None
    quality_profile_id: UUID | None = None
    enabled: bool | None = None
    minimum_score: Annotated[float | None, Field(ge=0)] = None

    @field_validator("query")
    @classmethod
    def strip_query(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("must not be blank")
        return value.strip() if value is not None else None

    @model_validator(mode="after")
    def has_a_change(self) -> MonitorUpdate:
        changed = {field for field in self.model_fields_set if getattr(self, field) is not None}
        if not changed:
            raise ValueError("at least one field must be supplied")
        if "query" in self.model_fields_set and self.query is None:
            raise ValueError("query cannot be null")
        if "quality_profile_id" in self.model_fields_set and self.quality_profile_id is None:
            raise ValueError("quality_profile_id cannot be null")
        return self


class MonitorResponse(BaseModel):
    id: UUID
    kind: MonitorKind
    performer_id: UUID | None
    studio_id: UUID | None
    query: str | None
    quality_profile_id: UUID
    enabled: bool
    minimum_score: float
    last_match_at: datetime | None


def monitor_response(monitor: Monitor) -> MonitorResponse:
    return MonitorResponse(
        id=monitor.id,
        kind=monitor.kind,
        performer_id=monitor.performer_id,
        studio_id=monitor.studio_id,
        query=monitor.query,
        quality_profile_id=monitor.quality_profile_id,
        enabled=monitor.enabled,
        minimum_score=monitor.minimum_score,
        last_match_at=monitor.last_match_at,
    )


async def monitor_or_404(session: AsyncSession, user_id: UUID, monitor_id: UUID) -> Monitor:
    monitor = await session.scalar(
        select(Monitor).where(Monitor.id == monitor_id, Monitor.user_id == user_id)
    )
    if monitor is None:
        raise HTTPException(status_code=404)
    return monitor


async def quality_profile_or_default(
    session: AsyncSession, quality_profile_id: UUID | None
) -> QualityProfile:
    if quality_profile_id is not None:
        profile = await session.get(QualityProfile, quality_profile_id)
    else:
        profile = await session.scalar(
            select(QualityProfile).where(QualityProfile.is_default.is_(True))
        )
    if profile is None:
        raise MonitorQualityProfileError("A quality profile is required for a monitor.")
    return profile


async def validate_target(session: AsyncSession, payload: MonitorCreate) -> None:
    if (
        payload.performer_id is not None
        and await session.get(Performer, payload.performer_id) is None
    ):
        raise HTTPException(status_code=404)
    if payload.studio_id is not None and await session.get(Studio, payload.studio_id) is None:
        raise HTTPException(status_code=404)


async def duplicate_monitor(
    session: AsyncSession,
    user_id: UUID,
    payload: MonitorCreate,
    *,
    excluded_id: UUID | None = None,
) -> bool:
    statement = select(Monitor.id).where(Monitor.user_id == user_id)
    if payload.performer_id is not None:
        statement = statement.where(Monitor.performer_id == payload.performer_id)
    elif payload.studio_id is not None:
        statement = statement.where(Monitor.studio_id == payload.studio_id)
    else:
        statement = statement.where(
            Monitor.normalized_query == normalize_release_title(payload.query or "")
        )
    if excluded_id is not None:
        statement = statement.where(Monitor.id != excluded_id)
    return await session.scalar(statement.limit(1)) is not None


async def flush_monitor(session: AsyncSession) -> None:
    """Translate a concurrent toggle's unique-constraint race into a stable error."""

    try:
        await session.flush()
    except IntegrityError as error:
        await session.rollback()
        raise MonitorDuplicateError("This monitor already exists.") from error


@router.get("", response_model=list[MonitorResponse])
async def list_monitors(user: CurrentUser, session: Session) -> list[MonitorResponse]:
    monitors = await session.scalars(
        select(Monitor).where(Monitor.user_id == user.id).order_by(Monitor.created_at.desc())
    )
    return [monitor_response(monitor) for monitor in monitors]


@router.post(
    "/{monitor_id}/backlog-search",
    status_code=202,
    response_class=Response,
    responses={404: {"model": ErrorResponse}},
)
async def trigger_backlog_search(
    monitor_id: UUID, request: HttpRequest, user: CurrentUser, session: Session
) -> Response:
    """Queue a fresh search for one monitor without waiting for its daily slot."""

    await monitor_or_404(session, user.id, monitor_id)
    minute = datetime.now(UTC).replace(second=0, microsecond=0).isoformat()
    await enqueue_once(
        request.app.state.queue,
        BACKLOG_SEARCH_JOB_NAME,
        str(monitor_id),
        f"manual:{minute}",
        queue=INDEXER_QUEUE,
    )
    return Response(status_code=202)


@router.post(
    "",
    response_model=MonitorResponse,
    status_code=201,
    responses={409: {"model": ErrorResponse}, 422: {"model": ErrorResponse}},
)
async def create_monitor(
    payload: MonitorCreate, user: CurrentUser, session: Session
) -> MonitorResponse:
    await validate_target(session, payload)
    profile = await quality_profile_or_default(session, payload.quality_profile_id)
    if await duplicate_monitor(session, user.id, payload):
        raise MonitorDuplicateError("This monitor already exists.")
    monitor = Monitor(
        user_id=user.id,
        kind=payload.kind,
        performer_id=payload.performer_id,
        studio_id=payload.studio_id,
        query=payload.query,
        normalized_query=normalize_release_title(payload.query)
        if payload.query is not None
        else None,
        quality_profile_id=profile.id,
        enabled=payload.enabled,
        minimum_score=payload.minimum_score,
    )
    session.add(monitor)
    await flush_monitor(session)
    return monitor_response(monitor)


@router.patch(
    "/{monitor_id}",
    response_model=MonitorResponse,
    responses={
        404: {"model": ErrorResponse},
        409: {"model": ErrorResponse},
        422: {"model": ErrorResponse},
    },
)
async def update_monitor(
    monitor_id: UUID, payload: MonitorUpdate, user: CurrentUser, session: Session
) -> MonitorResponse:
    monitor = await monitor_or_404(session, user.id, monitor_id)
    if payload.quality_profile_id is not None:
        profile = await quality_profile_or_default(session, payload.quality_profile_id)
        monitor.quality_profile_id = profile.id
    if payload.query is not None:
        if monitor.kind is not MonitorKind.QUERY:
            raise HTTPException(status_code=422)
        candidate = MonitorCreate(
            kind=MonitorKind.QUERY,
            query=payload.query,
            quality_profile_id=monitor.quality_profile_id,
        )
        if await duplicate_monitor(session, user.id, candidate, excluded_id=monitor.id):
            raise MonitorDuplicateError("This monitor already exists.")
        monitor.query = payload.query
        monitor.normalized_query = normalize_release_title(payload.query)
    if payload.enabled is not None:
        monitor.enabled = payload.enabled
    if payload.minimum_score is not None:
        monitor.minimum_score = payload.minimum_score
    await flush_monitor(session)
    return monitor_response(monitor)


@router.delete("/{monitor_id}", status_code=204, responses={404: {"model": ErrorResponse}})
async def delete_monitor(monitor_id: UUID, user: CurrentUser, session: Session) -> None:
    await session.delete(await monitor_or_404(session, user.id, monitor_id))
