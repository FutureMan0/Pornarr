"""Pure domain logic: normalisation, matching, quality decisions, estimation, deduplication, filters and scoring. This package performs no I/O — no database, no HTTP, no filesystem. That constraint is what makes every rule in it unit-testable."""

__all__ = ["PACKAGE_ROLE"]

PACKAGE_ROLE = "core"
