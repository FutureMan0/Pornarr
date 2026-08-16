"""Add library ownership, the watchlist and a short's source marker.

Revision ID: 0047
Revises: 0046
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0047"
down_revision: str | None = "0046"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "watchlist_entries",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("media_id", sa.Uuid(), nullable=False),
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
            ["media_id"],
            ["media.id"],
            name=op.f("fk_watchlist_entries_media_id_media"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_watchlist_entries_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_watchlist_entries")),
        sa.UniqueConstraint(
            "user_id", "media_id", name=op.f("uq_watchlist_entries_user_id_media_id")
        ),
    )
    op.create_index(
        op.f("ix_watchlist_entries_user_id"), "watchlist_entries", ["user_id"], unique=False
    )
    op.add_column("media", sa.Column("owner_id", sa.Uuid(), nullable=True))
    op.create_index(op.f("ix_media_owner_id"), "media", ["owner_id"], unique=False)
    op.create_foreign_key(
        op.f("fk_media_owner_id_users"), "media", "users", ["owner_id"], ["id"], ondelete="SET NULL"
    )
    op.add_column(
        "playback_progress", sa.Column("device_label", sa.String(length=64), nullable=True)
    )
    op.add_column("shorts", sa.Column("marker_id", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        op.f("fk_shorts_marker_id_scene_markers"),
        "shorts",
        "scene_markers",
        ["marker_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint(op.f("fk_shorts_marker_id_scene_markers"), "shorts", type_="foreignkey")
    op.drop_column("shorts", "marker_id")
    op.drop_column("playback_progress", "device_label")
    op.drop_constraint(op.f("fk_media_owner_id_users"), "media", type_="foreignkey")
    op.drop_index(op.f("ix_media_owner_id"), table_name="media")
    op.drop_column("media", "owner_id")
    op.drop_index(op.f("ix_watchlist_entries_user_id"), table_name="watchlist_entries")
    op.drop_table("watchlist_entries")
