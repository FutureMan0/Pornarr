"""Store root-space refresh state and daily user consumption.

Revision ID: 0027
Revises: 0026
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0027"
down_revision: str | None = "0026"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("root_folders", sa.Column("last_space_checked_at", sa.DateTime(timezone=True)))
    op.add_column(
        "root_folders",
        sa.Column("low_space_warning_sent", sa.Boolean(), server_default="false", nullable=False),
    )
    op.create_table(
        "daily_storage_usage",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("downloaded_bytes", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("download_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("user_id", "day"),
    )


def downgrade() -> None:
    op.drop_table("daily_storage_usage")
    op.drop_column("root_folders", "low_space_warning_sent")
    op.drop_column("root_folders", "last_space_checked_at")
