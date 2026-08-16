"""Shared Prometheus instruments for API and worker operations."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from time import perf_counter
from typing import Literal

from prometheus_client import (
    CONTENT_TYPE_LATEST,
    CollectorRegistry,
    Counter,
    Histogram,
    generate_latest,
)

REGISTRY = CollectorRegistry(auto_describe=True)
OPERATIONS_TOTAL = Counter(
    "pornarr_operations_total",
    "Completed Pornarr operations by kind and result.",
    ("operation", "result"),
    registry=REGISTRY,
)
OPERATION_SECONDS = Histogram(
    "pornarr_operation_seconds",
    "Pornarr operation duration by kind.",
    ("operation",),
    registry=REGISTRY,
)
Operation = Literal["search", "grab", "import", "transcode"]
OPERATION_NAMES: tuple[Operation, ...] = ("search", "grab", "import", "transcode")

for _operation in OPERATION_NAMES:
    for _result in ("ok", "error"):
        OPERATIONS_TOTAL.labels(operation=_operation, result=_result)
    OPERATION_SECONDS.labels(operation=_operation)


@contextmanager
def measure(operation: Operation) -> Iterator[None]:
    """Record duration and success/failure for a named operation."""
    started = perf_counter()
    try:
        yield
    except Exception:
        OPERATIONS_TOTAL.labels(operation=operation, result="error").inc()
        raise
    else:
        OPERATIONS_TOTAL.labels(operation=operation, result="ok").inc()
    finally:
        OPERATION_SECONDS.labels(operation=operation).observe(perf_counter() - started)


def render_metrics() -> tuple[bytes, str]:
    return generate_latest(REGISTRY), CONTENT_TYPE_LATEST
