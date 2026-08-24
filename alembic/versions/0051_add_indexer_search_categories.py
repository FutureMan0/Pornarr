"""Persist which categories an indexer's search/rss requests are restricted to.

Revision ID: 0050
Revises: 0049
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0051"
down_revision: str | None = "0050"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "indexers",
        sa.Column(
            "search_categories",
            sa.JSON(),
            server_default=sa.text("'[\"6000\"]'::json"),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("indexers", "search_categories")
