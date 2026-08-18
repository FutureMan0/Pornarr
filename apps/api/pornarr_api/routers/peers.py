"""Federation: the instances this one may read, and the pipe it reads them through.

Two surfaces, and the split between them is the security design.

`/admin/peers` is configuration. An administrator names another Pornarr, pastes
a key that the *other* instance issued, and tests it. The key is written through
:class:`~pornarr_db.types.EncryptedString` and is never read back out: it is not
in a response, not in an error, not in a log line. That is why a failed test
stores a short code such as ``unreachable`` rather than the exception text — an
httpx message contains the URL the key was sent to.

`/peers/{id}/proxy/{path}` is the pipe. The browser cannot talk to a peer
directly (it has no key, and it should never be given one), so this instance
fetches on its behalf. A proxy that forwards whatever it is handed is an open
relay, so the path is matched against a fixed allowlist of read paths and
everything else is 404 — including anything that would change state on the peer
beyond starting and stopping a transcode of the reader's own playback. Nothing
the peer answers is written to this database; a peer is a source of bytes to
forward, never a source of rows.
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator, Mapping
from datetime import UTC, date, datetime
from typing import Annotated
from uuid import UUID

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, SecretStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.background import BackgroundTask

from pornarr_api.auth import database_session, get_current_user, require_role
from pornarr_db.audit import write_audit
from pornarr_db.models.peer import Peer
from pornarr_db.models.user import User, UserRole
from pornarr_shared.errors import PornarrError

router = APIRouter(prefix="/admin/peers", tags=["peers"])
proxy_router = APIRouter(prefix="/peers", tags=["peers"])
Admin = Annotated[User, Depends(require_role(UserRole.ADMIN))]
CurrentUser = Annotated[User, Depends(get_current_user)]
Session = Annotated[AsyncSession, Depends(database_session)]

# A peer is somebody's home server on the other end of a tunnel, so it is slow
# in ways a local database never is. Every call is bounded: browsing must not
# hang because one household unplugged its NAS.
QUERY_TIMEOUT = httpx.Timeout(connect=3.0, read=8.0, write=8.0, pool=3.0)
# Playback is the one case where a long read is legitimate: the peer holds the
# response open while FFmpeg produces the next segment.
STREAM_TIMEOUT = httpx.Timeout(connect=3.0, read=60.0, write=30.0, pool=3.0)
MAX_PAGE_ITEMS = 100

_UUID = r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
# The exact asset names `transcode.hls_asset` will serve. Spelled out rather
# than accepting a filename, because a filename is how `..` gets in.
_HLS_ASSET = r"(?:master\.m3u8|variant\.m3u8|segment_[0-9]+\.ts)"

# The whole of what a peer may be asked for. Read paths, plus the three calls
# that drive a transcode of the reader's own playback and nothing else: no
# ratings, no requests, no administration, no writes to somebody's library.
ALLOWED_PATHS: tuple[tuple[str, re.Pattern[str]], ...] = (
    # The detail as well as the artwork: a card in a merged library has to be
    # openable, and its address on this instance does not exist here.
    ("GET", re.compile(rf"media/{_UUID}")),
    ("GET", re.compile(rf"media/{_UUID}/(?:poster|sprite|stream|playback-info)")),
    ("GET", re.compile(rf"transcode/sessions/{_UUID}/hls/{_HLS_ASSET}")),
    ("GET", re.compile(r"library")),
    ("GET", re.compile(r"search/local")),
    ("POST", re.compile(rf"transcode/media/{_UUID}/sessions")),
    ("POST", re.compile(rf"transcode/sessions/{_UUID}/heartbeat")),
    ("DELETE", re.compile(rf"transcode/sessions/{_UUID}")),
)

# Sent on to the peer. Deliberately excludes cookies and authorization: the
# reader's session belongs to this instance and means nothing on the other one.
_FORWARDED_REQUEST_HEADERS = frozenset(
    {"accept", "content-type", "if-none-match", "if-range", "range"}
)
# Returned to the reader. An allowlist, so a peer cannot set a cookie in a
# browser it has no relationship with, or send a redirect this instance would
# be lending its origin to.
_FORWARDED_RESPONSE_HEADERS = frozenset(
    {
        "accept-ranges",
        "cache-control",
        "content-encoding",
        "content-length",
        "content-range",
        "content-type",
        "etag",
        "expires",
        "last-modified",
        "vary",
    }
)


class PeerUnavailableError(PornarrError):
    """The peer did not answer, or answered with something unusable."""

    code = "PEER_UNAVAILABLE"
    status = 502


class PeerWrite(BaseModel):
    name: Annotated[str, Field(min_length=1, max_length=128)]
    base_url: Annotated[str, Field(min_length=1, max_length=512)]
    # A key the *remote* instance issued from its own account screen. Secret in
    # the model as well as in the column, so a stray log of the request body
    # prints `**********` instead.
    api_key: SecretStr
    enabled: bool = True


class PeerResponse(BaseModel):
    id: UUID
    name: str
    base_url: str
    enabled: bool
    health: str
    health_reason: str | None
    last_tested_at: datetime | None
    media_count: int | None


class RemoteLibraryItem(BaseModel):
    """One item as a peer described it.

    Everything a peer sends is untrusted, so it is parsed into this before it is
    allowed anywhere near a merge — including the numbers, because an out-of-range
    rating or a negative duration would sort into a position no local item can
    reach. The peer's own `poster_url` is not read at all; this instance builds
    those from the id so a peer cannot point a reader's browser at a third party.
    """

    model_config = ConfigDict(extra="ignore")

    id: UUID
    title: Annotated[str, Field(max_length=512)]
    studio: Annotated[str | None, Field(max_length=256)] = None
    release_date: date | None = None
    added_at: datetime
    duration_seconds: Annotated[float | None, Field(ge=0)] = None
    quality: Annotated[str | None, Field(max_length=64)] = None
    resolution: Annotated[str | None, Field(max_length=64)] = None
    rating: Annotated[float | None, Field(ge=0, le=5)] = None
    rating_count: Annotated[int, Field(ge=0)] = 0


class RemoteLibraryPage(BaseModel):
    model_config = ConfigDict(extra="ignore")

    items: Annotated[list[RemoteLibraryItem], Field(max_length=MAX_PAGE_ITEMS + 1)]
    total: Annotated[int, Field(ge=0)] = 0


def peer_response(peer: Peer) -> PeerResponse:
    return PeerResponse(
        id=peer.id,
        name=peer.name,
        base_url=peer.base_url,
        enabled=peer.enabled,
        health=peer.health,
        health_reason=peer.health_reason,
        last_tested_at=peer.last_tested_at,
        media_count=peer.media_count,
    )


async def peer_or_404(session: AsyncSession, peer_id: UUID) -> Peer:
    peer = await session.get(Peer, peer_id)
    if peer is None:
        raise HTTPException(status_code=404)
    return peer


async def enabled_peers(session: AsyncSession) -> list[Peer]:
    return list(
        await session.scalars(select(Peer).where(Peer.enabled.is_(True)).order_by(Peer.name))
    )


def peer_client(request: Request, timeout: httpx.Timeout) -> httpx.AsyncClient:
    """A client for one peer call.

    The transport comes from application state when a test installed one, which
    is how a second Pornarr can stand in for a peer without a socket.
    """
    return httpx.AsyncClient(
        transport=getattr(request.app.state, "peer_transport", None),
        timeout=timeout,
        follow_redirects=False,
    )


def peer_url(peer: Peer, path: str) -> str:
    return f"{peer.base_url.rstrip('/')}/{path}"


def proxy_path(peer_id: UUID, path: str) -> str:
    """The URL a browser uses for something that lives on a peer."""
    return f"/api/peers/{peer_id}/proxy/{path}"


def _failure_reason(error: Exception) -> str:
    """A short code for a failed call. Never the exception text."""
    if isinstance(error, httpx.TimeoutException):
        return "timed_out"
    if isinstance(error, httpx.HTTPError):
        return "unreachable"
    return "invalid_response"


async def probe_peer(request: Request, peer: Peer) -> tuple[str, str | None, int | None]:
    """Ask a peer for one item and report what it said.

    Returns the health, the short reason and the item count the peer reported.
    Nothing else from the answer is kept.
    """
    try:
        async with peer_client(request, QUERY_TIMEOUT) as client:
            response = await client.get(
                peer_url(peer, "library"),
                params={"limit": 1},
                headers={"X-Api-Key": peer.api_key},
            )
    except httpx.HTTPError as error:
        return "unhealthy", _failure_reason(error), None
    if response.status_code in {401, 403}:
        return "unhealthy", "unauthorized", None
    if response.status_code != 200:
        return "unhealthy", "invalid_response", None
    try:
        page = RemoteLibraryPage.model_validate(response.json())
    except ValueError:
        return "unhealthy", "invalid_response", None
    return "healthy", None, page.total


async def fetch_peer_page(
    request: Request, peer: Peer, params: Mapping[str, str]
) -> RemoteLibraryPage | None:
    """One page of a peer's library, or None if the peer is not usable.

    None rather than an exception: one unplugged household must degrade to "this
    peer is unavailable" beside a library that still browses, never to an error
    page for everybody else.
    """
    try:
        async with peer_client(request, QUERY_TIMEOUT) as client:
            response = await client.get(
                peer_url(peer, "library"),
                params=dict(params),
                headers={"X-Api-Key": peer.api_key},
            )
        if response.status_code != 200:
            return None
        return RemoteLibraryPage.model_validate(response.json())
    except (httpx.HTTPError, ValueError):
        # ValueError covers both a body that is not JSON and one that is JSON
        # but not this shape. Either way the peer is not answering usefully, and
        # half a page merged from it would page wrong for ever after.
        return None


@router.get("", response_model=list[PeerResponse])
async def list_peers(_: Admin, session: Session) -> list[PeerResponse]:
    peers = await session.scalars(select(Peer).order_by(Peer.name))
    return [peer_response(peer) for peer in peers]


@router.post("", response_model=PeerResponse, status_code=201)
async def create_peer(payload: PeerWrite, admin: Admin, session: Session) -> PeerResponse:
    base_url = payload.base_url.rstrip("/")
    if not base_url.startswith(("http://", "https://")):
        raise HTTPException(status_code=422)
    peer = Peer(
        name=payload.name,
        base_url=base_url,
        api_key=payload.api_key.get_secret_value(),
        enabled=payload.enabled,
    )
    session.add(peer)
    await session.flush()
    write_audit(session, actor_id=admin.id, action="peer.created", target=str(peer.id))
    return peer_response(peer)


@router.post("/{peer_id}/test", response_model=PeerResponse)
async def test_peer(peer_id: UUID, request: Request, _: Admin, session: Session) -> PeerResponse:
    peer = await peer_or_404(session, peer_id)
    health, reason, media_count = await probe_peer(request, peer)
    peer.health = health
    peer.health_reason = reason
    peer.last_tested_at = datetime.now(UTC)
    if media_count is not None:
        peer.media_count = media_count
    return peer_response(peer)


@router.delete("/{peer_id}", status_code=204)
async def delete_peer(peer_id: UUID, admin: Admin, session: Session) -> None:
    await session.delete(await peer_or_404(session, peer_id))
    write_audit(session, actor_id=admin.id, action="peer.deleted", target=str(peer_id))


def allowed(method: str, path: str) -> bool:
    """Whether this instance will ask a peer for `path` at all."""
    return any(
        method == allowed_method and pattern.fullmatch(path)
        for allowed_method, pattern in ALLOWED_PATHS
    )


async def _relay(request: Request, peer: Peer, path: str) -> StreamingResponse:
    client = peer_client(request, STREAM_TIMEOUT)
    outgoing = client.build_request(
        request.method,
        peer_url(peer, path),
        # The raw query string rather than a dict: a repeated parameter must
        # reach the peer as the reader sent it, not as the last value wins.
        params=httpx.QueryParams(request.url.query),
        headers={
            **{
                name: value
                for name, value in request.headers.items()
                if name.lower() in _FORWARDED_REQUEST_HEADERS
            },
            "X-Api-Key": peer.api_key,
        },
        content=request.stream() if request.method == "POST" else None,
    )
    try:
        response = await client.send(outgoing, stream=True)
    except httpx.HTTPError as error:
        await client.aclose()
        raise PeerUnavailableError("The peer did not answer.", peer_id=str(peer.id)) from error
    if response.status_code in {401, 403}:
        # The peer rejected *this instance's* key. Passing 401 through would tell
        # the reader their own session expired and send them to the login screen.
        await response.aclose()
        await client.aclose()
        raise PeerUnavailableError("The peer rejected this instance.", peer_id=str(peer.id))

    async def body() -> AsyncIterator[bytes]:
        # Raw bytes: whatever encoding the peer chose is passed through with its
        # header rather than decoded and re-encoded, which keeps a range request
        # byte-exact.
        async for chunk in response.aiter_raw():
            yield chunk

    async def release() -> None:
        await response.aclose()
        await client.aclose()

    return StreamingResponse(
        body(),
        status_code=response.status_code,
        headers={
            name: value
            for name, value in response.headers.items()
            if name.lower() in _FORWARDED_RESPONSE_HEADERS
        },
        background=BackgroundTask(release),
    )


async def _proxy(
    peer_id: UUID, path: str, request: Request, session: AsyncSession
) -> StreamingResponse:
    """Fetch one thing from a peer on the reader's behalf.

    A 404 for a path outside the allowlist, and for a peer that is disabled: a
    reader learns that this instance will not fetch it, not what the peer would
    have said.
    """
    peer = await peer_or_404(session, peer_id)
    if not peer.enabled or not allowed(request.method, path):
        raise HTTPException(status_code=404)
    return await _relay(request, peer, path)


# One route per method rather than one `api_route`: the generated client names a
# function after the operation, and three methods sharing a name is one symbol
# that can only do whichever the generator saw last.
@proxy_router.get("/{peer_id}/proxy/{path:path}")
async def proxy_get(
    peer_id: UUID, path: str, request: Request, _: CurrentUser, session: Session
) -> StreamingResponse:
    return await _proxy(peer_id, path, request, session)


@proxy_router.post("/{peer_id}/proxy/{path:path}")
async def proxy_post(
    peer_id: UUID, path: str, request: Request, _: CurrentUser, session: Session
) -> StreamingResponse:
    return await _proxy(peer_id, path, request, session)


@proxy_router.delete("/{peer_id}/proxy/{path:path}")
async def proxy_delete(
    peer_id: UUID, path: str, request: Request, _: CurrentUser, session: Session
) -> StreamingResponse:
    return await _proxy(peer_id, path, request, session)
