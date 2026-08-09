"""Authenticated request creation and lifecycle management."""

from __future__ import annotations

from typing import Annotated, Any, cast
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from fastapi import Request as HttpRequest
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from pornarr_api.auth import database_session, get_current_user
from pornarr_api.errors import ErrorResponse
from pornarr_db.models.download import DownloadJob
from pornarr_db.models.download_client import DownloadClient
from pornarr_db.models.request import Request, RequestHistory, RequestStatus
from pornarr_db.models.user import User, UserRole
from pornarr_db.requests import InvalidRequestTransitionError, transition_request
from pornarr_integrations.downloaders import DownloadClientCancellationAdapter
from pornarr_shared.errors import PornarrError

router = APIRouter(prefix="/requests", tags=["requests"])
CurrentUser = Annotated[User, Depends(get_current_user)]
Session = Annotated[AsyncSession, Depends(database_session)]

_TERMINAL_STATUSES = frozenset(
    {
        RequestStatus.AVAILABLE,
        RequestStatus.CANCELLED,
        RequestStatus.FAILED,
        RequestStatus.NOT_FOUND,
    }
)
_AUTHENTICATION_ERRORS: dict[int | str, dict[str, Any]] = {401: {"model": ErrorResponse}}


class RequestQuotaError(PornarrError):
    code = "REQUEST_QUOTA_EXCEEDED"
    status = 409


class RequestActionError(PornarrError):
    code = "REQUEST_ACTION_INVALID"
    status = 409


class RequestCancellationError(PornarrError):
    code = "REQUEST_CANCELLATION_FAILED"
    status = 502


class RequestCreate(BaseModel):
    query: Annotated[str, Field(min_length=1, max_length=512)]
    selected_release_guid: Annotated[str | None, Field(max_length=1024)] = None
    priority: Annotated[int, Field(ge=0)] = 50

    @field_validator("query")
    @classmethod
    def _strip_query(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("must not be blank")
        return stripped


class RequestPriorityWrite(BaseModel):
    priority: Annotated[int, Field(ge=0)]


class RequestHistoryResponse(BaseModel):
    status: RequestStatus


class RequestResponse(BaseModel):
    id: UUID
    user_id: UUID
    query: str
    selected_release_guid: str | None
    status: RequestStatus
    priority: int
    history: list[RequestHistoryResponse]


def request_response(request: Request, history: list[RequestHistory]) -> RequestResponse:
    return RequestResponse(
        id=request.id,
        user_id=request.user_id,
        query=request.query,
        selected_release_guid=request.selected_release_guid,
        status=request.status,
        priority=request.priority,
        history=[RequestHistoryResponse(status=item.status) for item in history],
    )


async def request_or_404(session: AsyncSession, user: User, request_id: UUID) -> Request:
    statement = select(Request).where(Request.id == request_id)
    if user.role is not UserRole.ADMIN:
        statement = statement.where(Request.user_id == user.id)
    request = await session.scalar(statement)
    if request is None:
        raise HTTPException(status_code=404)
    return request


async def request_history(session: AsyncSession, request_id: UUID) -> list[RequestHistory]:
    return list(
        await session.scalars(
            select(RequestHistory)
            .where(RequestHistory.request_id == request_id)
            .order_by(RequestHistory.created_at)
        )
    )


async def cancel_download_if_present(
    http_request: HttpRequest, session: AsyncSession, request: Request
) -> None:
    if request.download_job_id is None:
        return
    job = await session.get(DownloadJob, request.download_job_id)
    if job is None or job.download_client_id is None or job.client_job_id is None:
        return
    client = await session.get(DownloadClient, job.download_client_id)
    if client is None:
        return
    adapter = http_request.app.state.download_client_adapters.get(client.implementation)
    if adapter is None or not hasattr(adapter, "cancel"):
        raise RequestCancellationError("The download client cannot cancel this request.")
    try:
        await cast(DownloadClientCancellationAdapter, adapter).cancel(
            host=client.host,
            port=client.port,
            url_base=client.url_base,
            credentials=client.credentials,
            client_job_id=job.client_job_id,
        )
    except Exception as error:
        raise RequestCancellationError("The download client did not cancel the request.") from error


@router.get("", response_model=list[RequestResponse], responses=_AUTHENTICATION_ERRORS)
async def list_requests(
    user: CurrentUser, session: Session, status: RequestStatus | None = None
) -> list[RequestResponse]:
    statement = (
        select(Request).options(selectinload(Request.history)).order_by(Request.created_at.desc())
    )
    if user.role is not UserRole.ADMIN:
        statement = statement.where(Request.user_id == user.id)
    if status is not None:
        statement = statement.where(Request.status == status)
    requests = list(await session.scalars(statement))
    return [request_response(request, request.history) for request in requests]


@router.post(
    "",
    response_model=RequestResponse,
    status_code=201,
    responses={
        **_AUTHENTICATION_ERRORS,
        409: {"model": ErrorResponse},
        422: {"model": ErrorResponse},
    },
)
async def create_request(
    payload: RequestCreate, http_request: HttpRequest, user: CurrentUser, session: Session
) -> RequestResponse:
    maximum = http_request.app.state.settings.request_max_active_per_user
    if maximum:
        active_count = await session.scalar(
            select(func.count())
            .select_from(Request)
            .where(Request.user_id == user.id, Request.status.not_in(_TERMINAL_STATUSES))
        )
        if active_count is not None and active_count >= maximum:
            raise RequestQuotaError("The active request quota has been reached.")
    return await _create_request(payload, user, session)


async def _create_request(
    payload: RequestCreate, user: User, session: AsyncSession
) -> RequestResponse:
    status = RequestStatus.QUEUED if payload.selected_release_guid else RequestStatus.SEARCHING
    request = Request(
        user_id=user.id,
        query=payload.query,
        selected_release_guid=payload.selected_release_guid,
        status=status,
        priority=payload.priority,
    )
    session.add(request)
    await session.flush()
    history = RequestHistory(request_id=request.id, status=status)
    session.add(history)
    return request_response(request, [history])


@router.patch(
    "/{request_id}/priority",
    response_model=RequestResponse,
    responses={
        **_AUTHENTICATION_ERRORS,
        404: {"model": ErrorResponse},
        422: {"model": ErrorResponse},
    },
)
async def change_priority(
    request_id: UUID, payload: RequestPriorityWrite, user: CurrentUser, session: Session
) -> RequestResponse:
    request = await request_or_404(session, user, request_id)
    request.priority = payload.priority
    await session.flush()
    return request_response(request, await request_history(session, request.id))


@router.post(
    "/{request_id}/retry",
    response_model=RequestResponse,
    responses={
        **_AUTHENTICATION_ERRORS,
        404: {"model": ErrorResponse},
        409: {"model": ErrorResponse},
    },
)
async def retry_request(request_id: UUID, user: CurrentUser, session: Session) -> RequestResponse:
    request = await request_or_404(session, user, request_id)
    try:
        await transition_request(session, request, RequestStatus.SEARCHING)
    except InvalidRequestTransitionError as error:
        raise RequestActionError("This request cannot be retried.") from error
    await session.flush()
    return request_response(request, await request_history(session, request.id))


@router.post(
    "/{request_id}/cancel",
    response_model=RequestResponse,
    responses={
        **_AUTHENTICATION_ERRORS,
        404: {"model": ErrorResponse},
        409: {"model": ErrorResponse},
        502: {"model": ErrorResponse},
    },
)
async def cancel_request(
    request_id: UUID, http_request: HttpRequest, user: CurrentUser, session: Session
) -> RequestResponse:
    request = await request_or_404(session, user, request_id)
    try:
        await transition_request(session, request, RequestStatus.CANCELLED)
    except InvalidRequestTransitionError as error:
        raise RequestActionError("This request cannot be cancelled.") from error
    await cancel_download_if_present(http_request, session, request)
    await session.flush()
    return request_response(request, await request_history(session, request.id))
