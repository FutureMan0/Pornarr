"""Persist request-monitoring retry schedules.

Revision ID: 0034
Revises: 0033
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0034"
down_revision: str | None = "0033"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "requests", sa.Column("search_attempts", sa.Integer(), server_default="0", nullable=False)
    )
    op.add_column(
        "requests", sa.Column("next_search_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "requests", sa.Column("search_expires_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.create_index("ix_requests_search_schedule", "requests", ["status", "next_search_at"])


def downgrade() -> None:
    op.drop_index("ix_requests_search_schedule", table_name="requests")
    op.drop_column("requests", "search_expires_at")
    op.drop_column("requests", "next_search_at")
    op.drop_column("requests", "search_attempts")
