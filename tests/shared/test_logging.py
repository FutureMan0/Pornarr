from __future__ import annotations

import logging

import pytest

from pornarr_shared.logging import (
    REDACTED,
    RedactingFilter,
    install_redaction,
    redact,
    register_secret,
)


@pytest.fixture(autouse=True)
def _clear_registered_secrets() -> None:
    RedactingFilter.extra_values.clear()


@pytest.mark.parametrize(
    "line",
    [
        "api_key=abcd1234secret",
        "apiKey: abcd1234secret",
        'GET /search {"api_key": "abcd1234secret"}',
        "password=abcd1234secret",
        "client_secret = abcd1234secret",
        "Authorization: Bearer abcd1234secret",
        "connecting to https://user:abcd1234secret@qbittorrent:8080",
    ],
)
def test_credentials_are_scrubbed_from_common_shapes(line: str) -> None:
    scrubbed = redact(line)
    assert "abcd1234secret" not in scrubbed
    assert REDACTED in scrubbed


def test_ordinary_text_is_left_alone() -> None:
    line = "imported /data/library/studio/2026/scene/2160p/video.mkv in 1.4s"
    assert redact(line) == line


def test_registered_literal_is_scrubbed_even_without_a_surrounding_key() -> None:
    """A bare secret with no key= around it is invisible to pattern matching,
    which is why the values we know about are registered explicitly."""
    register_secret("s3cr3t-value")
    record = logging.LogRecord("t", logging.INFO, __file__, 1, "token is s3cr3t-value", None, None)

    RedactingFilter().filter(record)

    assert "s3cr3t-value" not in record.msg


def test_filter_scrubs_format_arguments_too() -> None:
    record = logging.LogRecord(
        "t", logging.INFO, __file__, 1, "calling %s", ("http://x?api_key=leaky",), None
    )

    RedactingFilter().filter(record)

    assert record.args is not None
    assert "leaky" not in record.getMessage()


def test_install_is_idempotent() -> None:
    logger = logging.getLogger("pornarr.test.install")
    logger.addHandler(logging.NullHandler())

    install_redaction(logger)
    install_redaction(logger)

    filters = [f for f in logger.handlers[0].filters if isinstance(f, RedactingFilter)]
    assert len(filters) == 1


def test_non_string_messages_pass_through_untouched() -> None:
    record = logging.LogRecord("t", logging.INFO, __file__, 1, {"a": 1}, None, None)
    assert RedactingFilter().filter(record) is True
    assert record.msg == {"a": 1}
