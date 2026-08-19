"""Authenticated request creation and lifecycle management."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Annotated, Any, cast
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response
from fastapi import Request as HttpRequest
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from pornarr_api.auth import ForbiddenError, database_session, get_current_user
from pornarr_api.errors import ErrorResponse
from pornarr_core.filters import FilterAction as CoreFilterAction
from pornarr_core.priorities import ADMIN_REQUEST_PRIORITY, USER_REQUEST_PRIORITY
from pornarr_db.audit import write_audit
from pornarr_db.download_clients import route_download_client
from pornarr_db.downloads import is_release_blocked
from pornarr_db.events import record_user_event
from pornarr_db.models.download import DownloadJob
from pornarr_db.models.download_client import DownloadClient
from pornarr_db.models.indexer import IndexerStats
from pornarr_db.models.media import Media
from pornarr_db.models.playback import UserEventType
from pornarr_db.models.release import ReleaseCache
from pornarr_db.models.request import Request, RequestHistory, RequestStatus
from pornarr_db.models.user import User, UserRole
from pornarr_db.release_filters import release_filter_decision
from pornarr_db.requests import InvalidRequestTransitionError, transition_request
from pornarr_db.settings import get_runtime_settings
from pornarr_integrations.downloaders import (
    DownloadClientCancellationAdapter,
    DownloadClientControlAdapter,
    DownloadClientPriorityAdapter,
)
from pornarr_integrations.submission import (
    ReleaseSubmissionError,
    UnsupportedReleaseError,
    release_protocol,
    submit_release,
)
from pornarr_shared.errors import PornarrError
from pornarr_shared.events import publish_event

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


class RequestControlError(PornarrError):
    code = "REQUEST_CONTROL_FAILED"
    status = 502


class RequestPriorityError(PornarrError):
    code = "REQUEST_PRIORITY_UPDATE_FAILED"
    status = 502


class RequestGrabError(PornarrError):
    code = "REQUEST_NOT_GRABBABLE"
    status = 409


class ReleaseExpiredError(PornarrError):
    code = "RELEASE_EXPIRED"
    status = 409


class ReleaseNotFoundError(PornarrError):
    code = "RELEASE_NOT_FOUND"
    status = 404


class ReleaseBlockedError(PornarrError):
    code = "RELEASE_BLOCKED"
    status = 409


class ReleaseInLibraryError(PornarrError):
    code = "RELEASE_IN_LIBRARY"
    status = 409


class ReleaseFilteredError(PornarrError):
    code = "RELEASE_FILTERED"
    status = 422


class ReleaseProtocolError(PornarrError):
    code = "RELEASE_PROTOCOL_UNSUPPORTED"
    status = 422


class GrabSubmissionError(PornarrError):
    code = "GRAB_SUBMISSION_FAILED"
    status = 502


class RequestCreate(BaseModel):
    query: Annotated[str, Field(min_length=1, max_length=512)]
    selected_release_guid: Annotated[str | None, Field(max_length=1024)] = None
    priority: Annotated[int, Field(ge=0, le=USER_REQUEST_PRIORITY)] = USER_REQUEST_PRIORITY
    # Which library the result should land in. Null means the requester's own,
    # which is what every existing client sends. Only an administrator may aim
    # an import at somebody else's shelf — see `resolve_target_owner`.
    target_owner_id: UUID | None = None

    @field_validator("query")
    @classmethod
    def _strip_query(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("must not be blank")
        return stripped


class RequestPriorityWrite(BaseModel):
    priority: Annotated[int, Field(ge=0, le=ADMIN_REQUEST_PRIORITY)]


class GrabWrite(BaseModel):
    release_id: UUID


class RequestHistoryResponse(BaseModel):
    status: RequestStatus


class RequestResponse(BaseModel):
    id: UUID
    user_id: UUID
    query: str
    selected_release_guid: str | None
    status: RequestStatus
    priority: int
    is_automatic: bool
    history: list[RequestHistoryResponse]


class GrabResponse(BaseModel):
    request_id: UUID
    download_job_id: UUID
    status: RequestStatus


def request_response(request: Request, history: list[RequestHistory]) -> RequestResponse:
    return RequestResponse(
        id=request.id,
        user_id=request.user_id,
        query=request.query,
        selected_release_guid=request.selected_release_guid,
        status=request.status,
        priority=request.priority,
        is_automatic=request.is_automatic,
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


async def control_download_if_present(
    http_request: HttpRequest, session: AsyncSession, request: Request, *, action: str
) -> None:
    if request.status in _TERMINAL_STATUSES:
        raise RequestActionError("This request can no longer be controlled.")
    if request.download_job_id is None:
        raise RequestActionError("This request has no download to control.")
    job = await session.get(DownloadJob, request.download_job_id)
    if job is None or job.download_client_id is None or job.client_job_id is None:
        raise RequestActionError("This request has no download to control.")
    client = await session.get(DownloadClient, job.download_client_id)
    if client is None:
        raise RequestActionError("This request has no download to control.")
    adapter = http_request.app.state.download_client_adapters.get(client.implementation)
    if adapter is None or not hasattr(adapter, action):
        raise RequestControlError(f"The download client cannot {action} this request.")
    try:
        controller = cast(DownloadClientControlAdapter, adapter)
        if action == "pause":
            await controller.pause(
                host=client.host,
                port=client.port,
                url_base=client.url_base,
                credentials=client.credentials,
                client_job_id=job.client_job_id,
            )
            job.status = "paused"
        else:
            await controller.resume(
                host=client.host,
                port=client.port,
                url_base=client.url_base,
                credentials=client.credentials,
                client_job_id=job.client_job_id,
            )
            job.status = "queued"
    except Exception as error:
        raise RequestControlError(f"The download client did not {action} the request.") from error


async def update_download_priority_if_present(
    http_request: HttpRequest, session: AsyncSession, request: Request, *, priority: int
) -> None:
    if request.download_job_id is None:
        return
    job = await session.get(DownloadJob, request.download_job_id)
    if job is None:
        return
    job.priority = priority
    if job.download_client_id is None or job.client_job_id is None:
        return
    client = await session.get(DownloadClient, job.download_client_id)
    if client is None:
        return
    adapter = http_request.app.state.download_client_adapters.get(client.implementation)
    if adapter is None or not hasattr(adapter, "set_priority"):
        return
    try:
        await cast(DownloadClientPriorityAdapter, adapter).set_priority(
            host=client.host,
            port=client.port,
            url_base=client.url_base,
            credentials=client.credentials,
            client_job_id=job.client_job_id,
            priority=priority,
        )
    except Exception as error:
        raise RequestPriorityError(
            "The download client did not update the request priority."
        ) from error


@router.get("", response_model=list[RequestResponse], responses=_AUTHENTICATION_ERRORS)
async def list_requests(
    user: CurrentUser, session: Session, status: RequestStatus | None = None
) -> list[RequestResponse]:
    statement = (
        select(Request)
        .options(selectinload(Request.history))
        .order_by(Request.priority.desc(), Request.created_at.desc())
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
        403: {"model": ErrorResponse},
        404: {"model": ErrorResponse},
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
    runtime_settings = await get_runtime_settings(session, http_request.app.state.settings)
    # Only an explicitly named target is recorded. Leaving it null keeps the
    # shared pool the destination on a server that never turned private
    # libraries on, which is where every request has landed so far.
    target_owner_id = (
        await resolve_target_owner(session, user, payload.target_owner_id)
        if payload.target_owner_id is not None
        else None
    )
    created = await _create_request(
        payload,
        user,
        session,
        request_search_max_age_days=runtime_settings.request_search_max_age_days,
        target_owner_id=target_owner_id,
    )
    await publish_event(
        http_request.app.state.redis,
        "request.created",
        {"request_id": str(created.id)},
        user_id=str(user.id),
    )
    return created


async def _create_request(
    payload: RequestCreate,
    user: User,
    session: AsyncSession,
    *,
    request_search_max_age_days: int | None = None,
    target_owner_id: UUID | None = None,
) -> RequestResponse:
    status = RequestStatus.QUEUED if payload.selected_release_guid else RequestStatus.SEARCHING
    now = datetime.now(UTC)
    request = Request(
        user_id=user.id,
        target_owner_id=target_owner_id,
        query=payload.query,
        selected_release_guid=payload.selected_release_guid,
        status=status,
        priority=payload.priority,
        next_search_at=now if payload.selected_release_guid is None else None,
        search_expires_at=(now + timedelta(days=request_search_max_age_days))
        if request_search_max_age_days is not None and payload.selected_release_guid is None
        else None,
    )
    session.add(request)
    await session.flush()
    history = RequestHistory(request_id=request.id, status=status)
    session.add(history)
    await record_user_event(session, user.id, UserEventType.REQUEST, value=float(payload.priority))
    return request_response(request, [history])


@router.post(
    "/{request_id}/grab",
    response_model=GrabResponse,
    status_code=201,
    responses={
        **_AUTHENTICATION_ERRORS,
        404: {"model": ErrorResponse},
        409: {"model": ErrorResponse},
        422: {"model": ErrorResponse},
        502: {"model": ErrorResponse},
    },
)
async def grab_release(
    request_id: UUID,
    payload: GrabWrite,
    http_request: HttpRequest,
    response: Response,
    user: CurrentUser,
    session: Session,
) -> GrabResponse:
    """Revalidate one cached release and submit it exactly once."""

    request = await request_or_404(session, user, request_id)
    if request.status not in {
        RequestStatus.SEARCHING,
        RequestStatus.RESULTS_FOUND,
        RequestStatus.QUEUED,
    }:
        raise RequestGrabError("This request cannot accept a release in its current state.")
    release = await session.scalar(
        select(ReleaseCache).where(ReleaseCache.id == payload.release_id).with_for_update()
    )
    if release is None:
        raise ReleaseNotFoundError("The selected release is not present in the cache.")

    existing = await session.scalar(
        select(DownloadJob).where(DownloadJob.release_guid == release.guid).limit(1)
    )
    if existing is not None:
        await _attach_grabbed_release(session, request, release.guid, existing.id)
        response.status_code = 200
        return GrabResponse(
            request_id=request.id,
            download_job_id=existing.id,
            status=request.status,
        )

    if release.expires_at <= datetime.now(release.expires_at.tzinfo):
        raise ReleaseExpiredError("The selected release has expired from the cache.")
    if await is_release_blocked(session, release.guid):
        raise ReleaseBlockedError("The selected release is temporarily blocked.")
    if await _library_has_release(session, release.normalized_title):
        raise ReleaseInLibraryError("The requested title is already in the library.")
    decision = await release_filter_decision(session, user.id, release)
    if decision.action is not CoreFilterAction.ALLOW:
        raise ReleaseFilteredError("The selected release is blocked by a content filter.")

    try:
        protocol = release_protocol(
            magnet_url=release.magnet_url, download_url=release.download_url
        )
    except UnsupportedReleaseError as error:
        raise ReleaseProtocolError(str(error)) from error
    client = await route_download_client(session, protocol)
    adapter = http_request.app.state.download_client_adapters.get(client.implementation)
    if adapter is None:
        raise ReleaseProtocolError("No compatible adapter is registered for the selected client.")
    try:
        client_job_id = await submit_release(
            adapter,
            protocol=protocol,
            host=client.host,
            port=client.port,
            url_base=client.url_base,
            credentials=client.credentials,
            category=client.category,
            priority=request.priority,
            magnet_url=release.magnet_url,
            info_hash=release.info_hash,
            download_url=release.download_url,
        )
    except UnsupportedReleaseError as error:
        raise ReleaseProtocolError(str(error)) from error
    except ReleaseSubmissionError as error:
        raise GrabSubmissionError(str(error)) from error
    job = DownloadJob(
        download_client_id=client.id,
        client_name=client.name,
        protocol=protocol,
        release_guid=release.guid,
        client_job_id=client_job_id,
        status="queued",
        priority=request.priority,
        size_bytes=release.size,
    )
    session.add(job)
    await session.flush()
    await _attach_grabbed_release(session, request, release.guid, job.id)
    stats = await session.get(IndexerStats, release.indexer_id)
    if stats is not None:
        stats.grabs += 1
    # Two facts, not one: the row now exists in the queue, and `submit_release`
    # above already handed it to the client - a download job can be shared by
    # more than one request (defect 10), so both are about the job rather than
    # scoped to this request the way `request.created` is.
    await publish_event(http_request.app.state.redis, "download.queued", {"job_id": str(job.id)})
    await publish_event(http_request.app.state.redis, "download.started", {"job_id": str(job.id)})
    return GrabResponse(request_id=request.id, download_job_id=job.id, status=request.status)


async def _attach_grabbed_release(
    session: AsyncSession, request: Request, release_guid: str, download_job_id: UUID
) -> None:
    request.selected_release_guid = release_guid
    request.download_job_id = download_job_id
    if request.status is RequestStatus.SEARCHING:
        await transition_request(session, request, RequestStatus.RESULTS_FOUND)
    if request.status is RequestStatus.RESULTS_FOUND:
        await transition_request(session, request, RequestStatus.QUEUED)


async def _library_has_release(session: AsyncSession, normalized_title: str) -> bool:
    return (
        await session.scalar(
            select(Media.id).where(Media.normalized_title == normalized_title).limit(1)
        )
        is not None
    )


@router.patch(
    "/{request_id}/priority",
    response_model=RequestResponse,
    responses={
        **_AUTHENTICATION_ERRORS,
        404: {"model": ErrorResponse},
        422: {"model": ErrorResponse},
        502: {"model": ErrorResponse},
    },
)
async def change_priority(
    request_id: UUID,
    payload: RequestPriorityWrite,
    http_request: HttpRequest,
    user: CurrentUser,
    session: Session,
) -> RequestResponse:
    request = await request_or_404(session, user, request_id)
    if user.role is not UserRole.ADMIN and payload.priority > USER_REQUEST_PRIORITY:
        raise ForbiddenError("Only an administrator can raise a request above user priority.")
    previous_priority = request.priority
    request.priority = payload.priority
    await update_download_priority_if_present(
        http_request, session, request, priority=payload.priority
    )
    if user.role is UserRole.ADMIN:
        write_audit(
            session,
            actor_id=user.id,
            action="request.priority.overridden",
            target=str(request.id),
            context={"previous_priority": previous_priority, "priority": payload.priority},
        )
    await session.flush()
    return request_response(request, await request_history(session, request.id))


@router.post(
    "/{request_id}/pause",
    response_model=RequestResponse,
    responses={
        **_AUTHENTICATION_ERRORS,
        404: {"model": ErrorResponse},
        409: {"model": ErrorResponse},
        502: {"model": ErrorResponse},
    },
)
async def pause_request(
    request_id: UUID, http_request: HttpRequest, user: CurrentUser, session: Session
) -> RequestResponse:
    request = await request_or_404(session, user, request_id)
    await control_download_if_present(http_request, session, request, action="pause")
    await session.flush()
    return request_response(request, await request_history(session, request.id))


@router.post(
    "/{request_id}/resume",
    response_model=RequestResponse,
    responses={
        **_AUTHENTICATION_ERRORS,
        404: {"model": ErrorResponse},
        409: {"model": ErrorResponse},
        502: {"model": ErrorResponse},
    },
)
async def resume_request(
    request_id: UUID, http_request: HttpRequest, user: CurrentUser, session: Session
) -> RequestResponse:
    request = await request_or_404(session, user, request_id)
    await control_download_if_present(http_request, session, request, action="resume")
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


async def resolve_target_owner(
    session: AsyncSession, requester: User, target_owner_id: UUID | None
) -> UUID:
    """Where an approved request should deposit its media.

    A guest may only fill their own library. Letting one guest push titles into
    another's would turn a private library into a shared inbox, which is the
    opposite of what the setting is for.
    """
    if target_owner_id is None or target_owner_id == requester.id:
        return requester.id
    if requester.role is not UserRole.ADMIN:
        raise HTTPException(status_code=403)
    target = await session.get(User, target_owner_id)
    if target is None or not target.is_active:
        raise HTTPException(status_code=404)
    return target_owner_id
