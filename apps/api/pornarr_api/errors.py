"""HTTP error handling.

Errors leave this application as objects with a stable machine-readable code, not
as English prose. The frontend maps codes to translated messages; a server-supplied
string cannot be translated and hardcodes English into every client.

See docs/api-contract.md.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.responses import Response

from pornarr_shared.errors import PornarrError


class ErrorResponse(BaseModel):
    """The contract shape for every machine-readable API error."""

    code: str
    status: int
    context: dict[str, Any]


# Codes for conditions Starlette raises before our own code sees the request.
_STATUS_CODES: dict[int, str] = {
    400: "BAD_REQUEST",
    401: "NOT_AUTHENTICATED",
    403: "FORBIDDEN",
    404: "NOT_FOUND",
    405: "METHOD_NOT_ALLOWED",
    409: "CONFLICT",
    413: "PAYLOAD_TOO_LARGE",
    422: "VALIDATION_FAILED",
    429: "RATE_LIMITED",
    500: "INTERNAL_ERROR",
    503: "SERVICE_UNAVAILABLE",
}


def error_body(code: str, status: int, **context: Any) -> dict[str, Any]:
    return {"code": code, "status": status, "context": context}


def _code_for_status(status: int) -> str:
    return _STATUS_CODES.get(status, "INTERNAL_ERROR")


async def handle_unexpected_error(request: Request, exc: Exception) -> JSONResponse:
    """Last resort. Never leaks the exception text to the client.

    An unexpected exception message can contain a connection string, a file path
    or a bound parameter. It belongs in the log, not in the response.
    """
    return JSONResponse(status_code=500, content=error_body("INTERNAL_ERROR", 500))


async def handle_pornarr_error(request: Request, exc: Exception) -> JSONResponse:
    # Narrowed rather than asserted: `python -O` strips asserts, and an exception
    # handler that raises AttributeError is worse than the original error.
    if not isinstance(exc, PornarrError):
        return await handle_unexpected_error(request, exc)
    return JSONResponse(status_code=exc.status, content=exc.as_dict())


async def handle_http_exception(request: Request, exc: Exception) -> Response:
    if not isinstance(exc, StarletteHTTPException):
        return await handle_unexpected_error(request, exc)

    # A 404 outside /api is a client route the router has never heard of, which
    # is normal for a single-page application. Handled here rather than as a
    # catch-all route, so real routes always win regardless of registration order.
    if exc.status_code == 404:
        from pornarr_api.spa import spa_response

        document = spa_response(request)
        if document is not None:
            return document

    return JSONResponse(
        status_code=exc.status_code,
        content=error_body(_code_for_status(exc.status_code), exc.status_code),
    )


async def handle_validation_error(request: Request, exc: Exception) -> JSONResponse:
    """Report which fields failed, without echoing the values.

    Echoing the submitted value into an error is how a password ends up in a log
    aggregator via the client.
    """
    if not isinstance(exc, RequestValidationError):
        return await handle_unexpected_error(request, exc)
    fields = [{"location": list(error["loc"]), "problem": error["msg"]} for error in exc.errors()]
    return JSONResponse(
        status_code=422,
        content=error_body("VALIDATION_FAILED", 422, fields=fields),
    )


def register_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(PornarrError, handle_pornarr_error)
    app.add_exception_handler(StarletteHTTPException, handle_http_exception)
    app.add_exception_handler(RequestValidationError, handle_validation_error)
    app.add_exception_handler(Exception, handle_unexpected_error)
