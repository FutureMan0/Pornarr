"""Add per-user playback progress and completion events.

Revision ID: 0017
Revises: 0016
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0017"
down_revision: str | None = "0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "playback_progress",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("media_id", sa.Uuid(), nullable=False),
        sa.Column("position_seconds", sa.Float(), nullable=False),
        sa.Column("duration_seconds", sa.Float(), nullable=False),
        sa.Column("completed", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.ForeignKeyConstraint(["media_id"], ["media.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_playback_progress")),
        sa.UniqueConstraint(
            "user_id", "media_id", name=op.f("uq_playback_progress_user_id_media_id")
        ),
    )
    op.create_index(
        "ix_playback_progress_user_id_completed_updated_at",
        "playback_progress",
        ["user_id", "completed", "updated_at"],
    )
    op.create_table(
        "user_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("media_id", sa.Uuid(), nullable=False),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["media_id"], ["media.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_user_events")),
        sa.UniqueConstraint(
            "user_id",
            "media_id",
            "event_type",
            name=op.f("uq_user_events_user_id_media_id_event_type"),
        ),
    )
    op.create_index("ix_user_events_user_id_created_at", "user_events", ["user_id", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_user_events_user_id_created_at", table_name="user_events")
    op.drop_table("user_events")
    op.drop_index(
        "ix_playback_progress_user_id_completed_updated_at", table_name="playback_progress"
    )
    op.drop_table("playback_progress")
