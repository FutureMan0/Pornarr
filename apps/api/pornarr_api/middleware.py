"""Request identification.

Every response carries an identifier and every log line emitted while handling
that request carries the same one. Without it, "the import failed for someone at
some point" is not traceable through an API, a worker and a download client.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from contextvars import ContextVar

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

REQUEST_ID_HEADER = "X-Request-Id"

_request_id: ContextVar[str] = ContextVar("request_id", default="-")


def current_request_id() -> str:
    """The identifier of the request being handled, or `-` outside a request."""
    return _request_id.get()


class RequestIdMiddleware(BaseHTTPMiddleware):
    """Assigns an identifier, or adopts one a reverse proxy already set.

    Adopting an inbound identifier is what makes a trace span the proxy, the API
    and everything downstream instead of restarting at our edge.
    """

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        incoming = request.headers.get(REQUEST_ID_HEADER)
        request_id = incoming if incoming and len(incoming) <= 128 else uuid.uuid4().hex
        token = _request_id.set(request_id)
        request.state.request_id = request_id
        try:
            response = await call_next(request)
        finally:
            _request_id.reset(token)
        response.headers[REQUEST_ID_HEADER] = request_id
        return response
