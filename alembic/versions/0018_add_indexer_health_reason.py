"""Persist the reason an indexer's circuit breaker opened.

Revision ID: 0018
Revises: 0017
Create Date: 2026-08-08
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0018"
down_revision: str | None = "0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("indexers", sa.Column("health_reason", sa.String(length=32), nullable=True))


def downgrade() -> None:
    op.drop_column("indexers", "health_reason")
