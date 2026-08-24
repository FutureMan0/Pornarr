"""Store quarantine metadata and administrator correction feedback.

Revision ID: 0041
Revises: 0040
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0041"
down_revision: str | None = "0040"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "quarantine_items",
        sa.Column("extracted_metadata", sa.JSON(), server_default=sa.text("'{}'"), nullable=False),
    )
    op.add_column(
        "quarantine_items",
        sa.Column("technical_details", sa.JSON(), server_default=sa.text("'{}'"), nullable=False),
    )
    op.alter_column("quarantine_items", "extracted_metadata", server_default=None)
    op.alter_column("quarantine_items", "technical_details", server_default=None)
    op.create_table(
        "metadata_corrections",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("source_title", sa.String(length=512), nullable=False),
        sa.Column("title", sa.String(length=512), nullable=False),
        sa.Column("studio", sa.String(length=256), nullable=True),
        sa.Column("release_date", sa.Date(), nullable=True),
        sa.Column("quality", sa.String(length=64), nullable=True),
        sa.Column("created_by_id", sa.Uuid(), nullable=True),
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
        sa.ForeignKeyConstraint(["created_by_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_metadata_corrections")),
        sa.UniqueConstraint("source_title", name=op.f("uq_metadata_corrections_source_title")),
    )


def downgrade() -> None:
    op.drop_table("metadata_corrections")
    op.drop_column("quarantine_items", "technical_details")
    op.drop_column("quarantine_items", "extracted_metadata")
