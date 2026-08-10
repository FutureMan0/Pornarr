"""Add a KNN trigram index for local search.

Revision ID: 0011
Revises: 0010
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        "ix_media_normalized_title_trgm_knn",
        "media",
        ["normalized_title"],
        postgresql_using="gist",
        postgresql_ops={"normalized_title": "gist_trgm_ops"},
    )


def downgrade() -> None:
    op.drop_index("ix_media_normalized_title_trgm_knn", table_name="media")
