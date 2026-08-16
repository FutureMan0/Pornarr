"""Persist reviewable quarantined imports.

Revision ID: 0040
Revises: 0039
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0040"
down_revision: str | None = "0039"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "quarantine_items",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("original_path", sa.String(length=1024), nullable=False),
        sa.Column("quarantine_path", sa.String(length=1024), nullable=False),
        sa.Column("reasons", sa.JSON(), nullable=False),
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
        sa.PrimaryKeyConstraint("id", name=op.f("pk_quarantine_items")),
        sa.UniqueConstraint("quarantine_path", name=op.f("uq_quarantine_items_quarantine_path")),
    )
    op.create_index("ix_quarantine_items_created_at", "quarantine_items", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_quarantine_items_created_at", table_name="quarantine_items")
    op.drop_table("quarantine_items")
