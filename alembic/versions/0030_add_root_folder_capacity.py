"""Store root capacity and automatic-download reservations.

Revision ID: 0030
Revises: 0029
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0030"
down_revision: str | None = "0029"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("root_folders", sa.Column("total_space_bytes", sa.BigInteger(), nullable=True))
    op.add_column(
        "daily_storage_usage",
        sa.Column("reserved_bytes", sa.BigInteger(), server_default="0", nullable=False),
    )
    op.add_column(
        "daily_storage_usage",
        sa.Column("reserved_download_count", sa.Integer(), server_default="0", nullable=False),
    )


def downgrade() -> None:
    op.drop_column("daily_storage_usage", "reserved_download_count")
    op.drop_column("daily_storage_usage", "reserved_bytes")
    op.drop_column("root_folders", "total_space_bytes")
