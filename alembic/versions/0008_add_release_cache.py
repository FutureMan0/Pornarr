"""Add normalized, expiring external release cache.

Revision ID: 0008
Revises: 0007
Create Date: 2026-08-08
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "release_cache",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("indexer_id", sa.Uuid(), nullable=False),
        sa.Column("guid", sa.String(length=1024), nullable=False),
        sa.Column("title", sa.String(length=1024), nullable=False),
        sa.Column("normalized_title", sa.String(length=1024), nullable=False),
        sa.Column("details_url", sa.Text(), nullable=True),
        sa.Column("download_url", sa.String(length=1024), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("size", sa.BigInteger(), nullable=True),
        sa.Column("categories", sa.JSON(), nullable=False),
        sa.Column("seeders", sa.Integer(), nullable=True),
        sa.Column("peers", sa.Integer(), nullable=True),
        sa.Column("info_hash", sa.String(length=128), nullable=True),
        sa.Column("magnet_url", sa.Text(), nullable=True),
        sa.Column("groups", sa.JSON(), nullable=False),
        sa.Column("poster", sa.String(length=512), nullable=True),
        sa.Column("parts", sa.Integer(), nullable=True),
        sa.Column("password_protected", sa.Boolean(), nullable=True),
        sa.Column("raw_payload", sa.JSON(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
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
            name=op.f("fk_release_cache_indexer_id_indexers"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_release_cache")),
    )
    op.create_index(
        "uq_release_cache_indexer_guid", "release_cache", ["indexer_id", "guid"], unique=True
    )
    op.create_index(
        "ix_release_cache_indexer_expires_at", "release_cache", ["indexer_id", "expires_at"]
    )
    op.create_index(
        "ix_release_cache_normalized_title_trgm",
        "release_cache",
        ["normalized_title"],
        postgresql_using="gin",
        postgresql_ops={"normalized_title": "gin_trgm_ops"},
    )


def downgrade() -> None:
    op.drop_index("ix_release_cache_normalized_title_trgm", table_name="release_cache")
    op.drop_index("ix_release_cache_indexer_expires_at", table_name="release_cache")
    op.drop_index("uq_release_cache_indexer_guid", table_name="release_cache")
    op.drop_table("release_cache")
