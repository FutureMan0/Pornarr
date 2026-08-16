"""Add persisted recommendation candidates.

Revision ID: 0033
Revises: 0032
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0033"
down_revision: str | None = "0032"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "recommendation_candidates",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("media_id", sa.Uuid(), nullable=False),
        sa.Column("score", sa.Float(), nullable=False),
        sa.Column("reason", sa.JSON(), nullable=False),
        sa.Column("model_version", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["media_id"], ["media.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_recommendation_candidates")),
        sa.UniqueConstraint(
            "user_id", "media_id", name=op.f("uq_recommendation_candidates_user_id_media_id")
        ),
    )
    op.create_index(
        "ix_recommendation_candidates_user_id_score",
        "recommendation_candidates",
        ["user_id", "score"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_recommendation_candidates_user_id_score", table_name="recommendation_candidates"
    )
    op.drop_table("recommendation_candidates")
