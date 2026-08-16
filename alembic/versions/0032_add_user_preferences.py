"""Add incremental user interest preferences.

Revision ID: 0032
Revises: 0031
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0032"
down_revision: str | None = "0031"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "user_preferences",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("axis", sa.String(length=32), nullable=False),
        sa.Column("subject", sa.String(length=256), nullable=False),
        sa.Column("raw_score", sa.Float(), nullable=False),
        sa.Column("score", sa.Float(), nullable=False),
        sa.Column(
            "computed_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("user_id", "axis", "subject", name=op.f("pk_user_preferences")),
    )
    op.create_index(
        "ix_user_preferences_user_id_axis_score",
        "user_preferences",
        ["user_id", "axis", "score"],
    )
    op.create_table(
        "user_preference_states",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("last_event_created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_event_id", sa.Uuid(), nullable=True),
        sa.Column(
            "computed_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("user_id", name=op.f("pk_user_preference_states")),
    )


def downgrade() -> None:
    op.drop_table("user_preference_states")
    op.drop_index("ix_user_preferences_user_id_axis_score", table_name="user_preferences")
    op.drop_table("user_preferences")
