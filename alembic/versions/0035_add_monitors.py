"""Add user-owned performer, studio, and query monitors.

Revision ID: 0035
Revises: 0034
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0035"
down_revision: str | None = "0034"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

monitor_kind = postgresql.ENUM(
    "performer", "studio", "query", name="monitor_kind", create_type=False
)


def upgrade() -> None:
    monitor_kind.create(op.get_bind(), checkfirst=True)
    op.create_table(
        "monitors",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("kind", monitor_kind, nullable=False),
        sa.Column("performer_id", sa.Uuid(), nullable=True),
        sa.Column("studio_id", sa.Uuid(), nullable=True),
        sa.Column("query", sa.String(length=512), nullable=True),
        sa.Column("normalized_query", sa.String(length=512), nullable=True),
        sa.Column("quality_profile_id", sa.Uuid(), nullable=False),
        sa.Column("enabled", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("minimum_score", sa.Float(), server_default="0", nullable=False),
        sa.Column("last_match_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.CheckConstraint(
            "(kind = 'performer' AND performer_id IS NOT NULL AND studio_id IS NULL "
            "AND query IS NULL AND normalized_query IS NULL) OR "
            "(kind = 'studio' AND performer_id IS NULL AND studio_id IS NOT NULL "
            "AND query IS NULL AND normalized_query IS NULL) OR "
            "(kind = 'query' AND performer_id IS NULL AND studio_id IS NULL "
            "AND query IS NOT NULL AND normalized_query IS NOT NULL)",
            name=op.f("ck_monitors_monitor_has_exactly_one_target"),
        ),
        sa.CheckConstraint(
            "minimum_score >= 0", name=op.f("ck_monitors_minimum_score_not_negative")
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["performer_id"], ["performers.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["studio_id"], ["studios.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["quality_profile_id"], ["quality_profiles.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_monitors")),
        sa.UniqueConstraint(
            "user_id", "performer_id", name=op.f("uq_monitors_user_id_performer_id")
        ),
        sa.UniqueConstraint("user_id", "studio_id", name=op.f("uq_monitors_user_id_studio_id")),
        sa.UniqueConstraint(
            "user_id", "normalized_query", name=op.f("uq_monitors_user_id_normalized_query")
        ),
    )


def downgrade() -> None:
    op.drop_table("monitors")
    monitor_kind.drop(op.get_bind(), checkfirst=True)
