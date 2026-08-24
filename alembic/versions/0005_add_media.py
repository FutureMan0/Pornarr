"""Add logical media, physical files and replacement history.

Revision ID: 0005
Revises: 0004
Create Date: 2026-08-07
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "media",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("title", sa.String(length=512), nullable=False),
        sa.Column("normalized_title", sa.String(length=512), nullable=False),
        sa.Column("description", sa.String(), nullable=True),
        sa.Column("studio", sa.String(length=256), nullable=True),
        sa.Column("release_date", sa.Date(), nullable=True),
        sa.Column("status", sa.String(length=32), server_default="available", nullable=False),
        sa.Column("confidence", sa.Float(), nullable=True),
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
        sa.PrimaryKeyConstraint("id", name=op.f("pk_media")),
    )
    op.create_index(
        "ix_media_normalized_title_trgm",
        "media",
        ["normalized_title"],
        postgresql_using="gin",
        postgresql_ops={"normalized_title": "gin_trgm_ops"},
    )
    op.create_table(
        "media_files",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("media_id", sa.Uuid(), nullable=False),
        sa.Column("path", sa.String(length=1024), nullable=False),
        sa.Column("size", sa.BigInteger(), nullable=False),
        sa.Column("codecs", sa.JSON(), nullable=True),
        sa.Column("resolution", sa.String(length=32), nullable=True),
        sa.Column("quality", sa.String(length=64), nullable=True),
        sa.Column("custom_format_score", sa.Integer(), server_default="0", nullable=False),
        sa.Column("oshash", sa.String(length=64), nullable=True),
        sa.Column("is_active", sa.Boolean(), server_default="true", nullable=False),
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
            name=op.f("fk_media_files_media_id_media"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_media_files")),
    )
    op.create_index(
        "uq_media_files_active_media",
        "media_files",
        ["media_id"],
        unique=True,
        postgresql_where=sa.text("is_active"),
    )
    op.create_table(
        "media_file_history",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("replaced_file_id", sa.Uuid(), nullable=False),
        sa.Column("replacement_file_id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["replaced_file_id"],
            ["media_files.id"],
            name=op.f("fk_media_file_history_replaced_file_id_media_files"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["replacement_file_id"],
            ["media_files.id"],
            name=op.f("fk_media_file_history_replacement_file_id_media_files"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_media_file_history")),
    )


def downgrade() -> None:
    op.drop_table("media_file_history")
    op.drop_index("uq_media_files_active_media", table_name="media_files")
    op.drop_table("media_files")
    op.drop_index("ix_media_normalized_title_trgm", table_name="media")
    op.drop_table("media")
