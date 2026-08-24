"""Add disabled user automation rules.

Revision ID: 0026
Revises: 0025
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0026"
down_revision: str | None = "0025"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "automation_rules",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("enabled", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("minimum_score", sa.Float(), server_default="0", nullable=False),
        sa.Column("daily_download_limit_gb", sa.Integer(), server_default="10", nullable=False),
        sa.Column("max_concurrent_jobs", sa.Integer(), server_default="2", nullable=False),
        sa.Column("max_downloads_per_day", sa.Integer(), server_default="3", nullable=False),
        sa.Column("allowed_qualities", sa.JSON(), nullable=False),
        sa.Column("blocked_tags", sa.JSON(), nullable=False),
        sa.Column("blocked_performers", sa.JSON(), nullable=False),
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
            ["user_id"],
            ["users.id"],
            name=op.f("fk_automation_rules_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("user_id", name=op.f("pk_automation_rules")),
    )
    op.execute(
        """
        INSERT INTO automation_rules (user_id, allowed_qualities, blocked_tags, blocked_performers)
        SELECT id, '[]'::json, '[]'::json, '[]'::json FROM users
        """
    )


def downgrade() -> None:
    op.drop_table("automation_rules")
