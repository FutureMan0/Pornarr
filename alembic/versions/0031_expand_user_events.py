"""Expand user events for recommendations and privacy retention.

Revision ID: 0031
Revises: 0030
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0031"
down_revision: str | None = "0030"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint(
        op.f("uq_user_events_user_id_media_id_event_type"), "user_events", type_="unique"
    )
    op.alter_column("user_events", "media_id", existing_type=sa.Uuid(), nullable=True)
    op.add_column("user_events", sa.Column("value", sa.Float(), nullable=True))
    op.add_column("user_events", sa.Column("subject_id", sa.Uuid(), nullable=True))
    op.execute(
        "UPDATE user_events SET event_type = 'completed' WHERE event_type = 'playback.completed'"
    )


def downgrade() -> None:
    op.execute("DELETE FROM user_events WHERE media_id IS NULL")
    op.execute(
        "UPDATE user_events SET event_type = 'playback.completed' WHERE event_type = 'completed'"
    )
    op.execute(
        """
        DELETE FROM user_events
        WHERE id IN (
            SELECT id FROM (
                SELECT id, row_number() OVER (
                    PARTITION BY user_id, media_id, event_type ORDER BY created_at, id
                ) AS position
                FROM user_events
            ) AS duplicates
            WHERE position > 1
        )
        """
    )
    op.drop_column("user_events", "subject_id")
    op.drop_column("user_events", "value")
    op.alter_column("user_events", "media_id", existing_type=sa.Uuid(), nullable=False)
    op.create_unique_constraint(
        op.f("uq_user_events_user_id_media_id_event_type"),
        "user_events",
        ["user_id", "media_id", "event_type"],
    )
