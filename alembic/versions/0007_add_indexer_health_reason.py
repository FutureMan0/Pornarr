"""Persist the reason an indexer's circuit breaker opened.

Revision ID: 0007
Revises: 0006
Create Date: 2026-08-08
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("indexers", sa.Column("health_reason", sa.String(length=32), nullable=True))


def downgrade() -> None:
    op.drop_column("indexers", "health_reason")
