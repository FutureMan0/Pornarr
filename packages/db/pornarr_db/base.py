"""Declarative base and shared column conventions.

The naming convention is the load-bearing part of this module. PostgreSQL
generates its own names for unnamed constraints and indexes; Alembic autogenerate
then cannot match them against the models and produces spurious drop-and-recreate
migrations. Fixing that later means renaming every constraint in a live database.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import DateTime, MetaData, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_N_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    """Base for every model in the application."""

    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class TimestampMixin:
    """`created_at` and `updated_at`, both timezone-aware and set by the database.

    Set server-side rather than in Python: a worker and an API instance do not
    share a clock, and rows written by a migration would otherwise have no
    timestamps at all.
    """

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


def utcnow() -> datetime:
    """Timezone-aware now, for the places that genuinely need a Python value."""
    return datetime.now(UTC)
