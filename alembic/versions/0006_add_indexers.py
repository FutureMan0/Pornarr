"""Add configured indexers and their aggregate statistics.

Revision ID: 0006
Revises: 0005
Create Date: 2026-08-08
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "indexers",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("protocol", sa.String(length=32), nullable=False),
        sa.Column("implementation", sa.String(length=64), nullable=False),
        sa.Column("base_url", sa.String(length=512), nullable=False),
        sa.Column("api_key", sa.String(length=1024), nullable=False),
        sa.Column("categories", sa.JSON(), nullable=False),
        sa.Column("priority", sa.Integer(), server_default="0", nullable=False),
        sa.Column("enabled", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("health", sa.String(length=32), nullable=False),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("last_tested_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.PrimaryKeyConstraint("id", name=op.f("pk_indexers")),
        sa.UniqueConstraint("name", name=op.f("uq_indexers_name")),
    )
    op.create_table(
        "indexer_stats",
        sa.Column("indexer_id", sa.Uuid(), nullable=False),
        sa.Column("queries", sa.Integer(), server_default="0", nullable=False),
        sa.Column("failures", sa.Integer(), server_default="0", nullable=False),
        sa.Column("average_latency_ms", sa.Float(), nullable=True),
        sa.Column("grabs", sa.Integer(), server_default="0", nullable=False),
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
        sa.ForeignKeyConstraint(
            ["indexer_id"],
            ["indexers.id"],
            name=op.f("fk_indexer_stats_indexer_id_indexers"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("indexer_id", name=op.f("pk_indexer_stats")),
    )


def downgrade() -> None:
    op.drop_table("indexer_stats")
    op.drop_table("indexers")
