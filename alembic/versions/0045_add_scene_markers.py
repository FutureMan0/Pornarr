"""Add detected scene markers.

Revision ID: 0045
Revises: 0044
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0045"
down_revision: str | None = "0044"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "scene_markers",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("media_file_id", sa.Uuid(), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("start_seconds", sa.Float(), nullable=False),
        sa.Column("end_seconds", sa.Float(), nullable=False),
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
            "end_seconds > start_seconds", name=op.f("ck_scene_markers_end_after_start")
        ),
        sa.CheckConstraint("ordinal >= 0", name=op.f("ck_scene_markers_ordinal_not_negative")),
        sa.CheckConstraint("start_seconds >= 0", name=op.f("ck_scene_markers_start_not_negative")),
        sa.ForeignKeyConstraint(
            ["media_file_id"],
            ["media_files.id"],
            name=op.f("fk_scene_markers_media_file_id_media_files"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_scene_markers")),
        sa.UniqueConstraint(
            "media_file_id", "ordinal", name=op.f("uq_scene_markers_media_file_id_ordinal")
        ),
    )
    op.create_index(
        op.f("ix_scene_markers_media_file_id"), "scene_markers", ["media_file_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_scene_markers_media_file_id"), table_name="scene_markers")
    op.drop_table("scene_markers")
