"""Log redaction.

Two rules from the plan that this enforces mechanically rather than by review:
no secret reaches a log, and no user's media preferences reach a log in clear
text. Both are easy to break with one convenient f-string.

Full structured logging arrives with the hardening milestone; this is the
redaction layer everything else logs through.
"""

from __future__ import annotations

import json
import logging
import re
from contextvars import ContextVar, Token
from datetime import UTC, datetime
from typing import Any, ClassVar

REDACTED = "***"
_request_id: ContextVar[str] = ContextVar("request_id", default="-")
_job_id: ContextVar[str | None] = ContextVar("job_id", default=None)

# Matches `key=value`, `key: value` and `"key": "value"` for sensitive names, in
# whatever quoting a formatter happened to produce.
# `authorization` is deliberately absent: its value is a scheme followed by the
# token, so the generic key pattern would stop at the scheme and leave the token
# in the log. It gets a dedicated pattern that runs first.
_SENSITIVE_KEY = (
    r"(?:api[_-]?key|apikey|password|passwd|secret|token|"
    r"client[_-]?secret|cookie|session[_-]?id)"
)
_PREFERENCE = re.compile(r"\b(?:media[_-]?)?preferences?\b", re.IGNORECASE)
# Each pattern carries its own replacement. Inferring the replacement from the
# group count is how the value ends up back in the output: in `key=value` the
# second group IS the secret, while in a JSON pair it is the closing quote.
_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    # Authorization: Bearer <token> / Authorization=Basic <blob>. Must run
    # before the generic key pattern, which would only consume the scheme.
    (
        re.compile(r"(Authorization\s*[=:]\s*)(?:\w+\s+)?\S+", re.IGNORECASE),
        rf"\g<1>{REDACTED}",
    ),
    # {"api_key": "value"} — group 2 is the closing quote and must be kept.
    (
        re.compile(rf'("{_SENSITIVE_KEY}"\s*:\s*")[^"]*(")', re.IGNORECASE),
        rf"\g<1>{REDACTED}\g<2>",
    ),
    # api_key=value / password: value — everything after the separator goes.
    (
        re.compile(rf"({_SENSITIVE_KEY}\s*[=:]\s*)\S+", re.IGNORECASE),
        rf"\g<1>{REDACTED}",
    ),
    # Authorization: Bearer <token>
    (re.compile(r"(Bearer\s+)\S+", re.IGNORECASE), rf"\g<1>{REDACTED}"),
    # https://user:password@host — the trailing @ must survive.
    (re.compile(r"(://[^:/@\s]+:)[^@\s]+(@)"), rf"\g<1>{REDACTED}\g<2>"),
)


def redact(text: str) -> str:
    """Replace anything that looks like a credential with a placeholder."""
    if _PREFERENCE.search(text):
        return REDACTED
    result = text
    for pattern, replacement in _PATTERNS:
        result = pattern.sub(replacement, result)
    return result


class RedactingFilter(logging.Filter):
    """Scrubs credentials from every record passing through a handler.

    Attached to handlers rather than loggers, so a library that logs a request
    URL containing an API key is covered too — that is the case review tends to
    miss.
    """

    extra_values: ClassVar[set[str]] = set()

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = self._scrub(record.msg)
        if record.args:
            record.args = self._scrub_args(record.args)
        return True

    def _scrub(self, value: Any) -> Any:
        if not isinstance(value, str):
            return value
        scrubbed = redact(value)
        for literal in self.extra_values:
            if literal:
                scrubbed = scrubbed.replace(literal, REDACTED)
        return scrubbed

    def _scrub_args(self, args: Any) -> Any:
        if isinstance(args, dict):
            return {key: self._scrub(value) for key, value in args.items()}
        if isinstance(args, tuple):
            return tuple(self._scrub(value) for value in args)
        return args


def register_secret(value: str) -> None:
    """Redact a specific literal wherever it appears.

    Pattern matching cannot catch a bare secret logged with no surrounding key,
    so the values we know about are registered explicitly at startup.
    """
    if value:
        RedactingFilter.extra_values.add(value)


def install_redaction(logger: logging.Logger | None = None) -> None:
    """Attach the filter to every handler of the given logger, root by default."""
    target = logger or logging.getLogger()
    redactor = RedactingFilter()
    for handler in target.handlers:
        if not any(isinstance(existing, RedactingFilter) for existing in handler.filters):
            handler.addFilter(redactor)


def current_request_id() -> str:
    return _request_id.get()


def set_request_id(value: str) -> Token[str]:
    return _request_id.set(value)


def reset_request_id(token: Token[str]) -> None:
    _request_id.reset(token)


def current_job_id() -> str | None:
    return _job_id.get()


def set_job_id(value: str) -> Token[str | None]:
    return _job_id.set(value)


def reset_job_id(token: Token[str | None]) -> None:
    _job_id.reset(token)


class JsonFormatter(logging.Formatter):
    """Machine-readable records with request correlation and no implicit extras."""

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname.lower(),
            "logger": record.name,
            "message": RedactingFilter()._scrub(record.getMessage()),
            "request_id": current_request_id(),
            "job_id": current_job_id(),
        }
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def configure_logging(level: str) -> None:
    """Apply the current runtime level and structured formatter to root handlers."""
    root = logging.getLogger()
    root.setLevel(level.upper())
    if not root.handlers:
        root.addHandler(logging.StreamHandler())
    for handler in root.handlers:
        handler.setFormatter(JsonFormatter())
    install_redaction(root)
