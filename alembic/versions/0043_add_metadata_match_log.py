"""Record every metadata cascade attempt.

Revision ID: 0043
Revises: 0042
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0043"
down_revision: str | None = "0042"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "metadata_match_log",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("import_trigger_id", sa.Uuid(), nullable=True),
        sa.Column("provider", sa.String(length=128), nullable=False),
        sa.Column("tier", sa.String(length=32), nullable=False),
        sa.Column("outcome", sa.String(length=32), nullable=False),
        sa.Column("query", sa.JSON(), nullable=False),
        sa.Column("result", sa.JSON(), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("detail", sa.Text(), nullable=True),
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
        sa.ForeignKeyConstraint(["import_trigger_id"], ["import_triggers.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_metadata_match_log")),
    )
    op.create_index(
        "ix_metadata_match_log_import_trigger_id",
        "metadata_match_log",
        ["import_trigger_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_metadata_match_log_import_trigger_id", table_name="metadata_match_log")
    op.drop_table("metadata_match_log")
