"""Remember the newest release seen from each indexer's feed.

Revision ID: 0036
Revises: 0035
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0036"
down_revision: str | None = "0035"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("indexers", sa.Column("last_rss_guid", sa.String(length=1024), nullable=True))


def downgrade() -> None:
    op.drop_column("indexers", "last_rss_guid")
