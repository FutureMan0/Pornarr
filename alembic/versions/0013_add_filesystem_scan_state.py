"""Add filesystem scanner state to media files.

Revision ID: 0013
Revises: 0012
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0013"
down_revision: str | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "media_files",
        sa.Column("modified_at_ns", sa.BigInteger(), server_default="0", nullable=False),
    )
    op.add_column(
        "media_files",
        sa.Column("is_missing", sa.Boolean(), server_default="false", nullable=False),
    )
    op.create_unique_constraint(op.f("uq_media_files_path"), "media_files", ["path"])


def downgrade() -> None:
    op.drop_constraint(op.f("uq_media_files_path"), "media_files", type_="unique")
    op.drop_column("media_files", "is_missing")
    op.drop_column("media_files", "modified_at_ns")
