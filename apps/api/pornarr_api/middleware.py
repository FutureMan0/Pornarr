"""Request identification.

Every response carries an identifier and every log line emitted while handling
that request carries the same one. Without it, "the import failed for someone at
some point" is not traceable through an API, a worker and a download client.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Awaitable, Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from pornarr_shared.logging import current_request_id as _current_request_id
from pornarr_shared.logging import reset_request_id, set_request_id

REQUEST_ID_HEADER = "X-Request-Id"
logger = logging.getLogger(__name__)


def current_request_id() -> str:
    """The request identifier made available to existing API callers."""
    return _current_request_id()


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
        token = set_request_id(request_id)
        request.state.request_id = request_id
        try:
            response = await call_next(request)
            logger.info(
                "request completed: %s %s %s",
                request.method,
                request.url.path,
                response.status_code,
            )
        finally:
            reset_request_id(token)
        response.headers[REQUEST_ID_HEADER] = request_id
        return response
