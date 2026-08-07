"""Index media OpenSubtitles hashes.

Revision ID: 0007
Revises: 0006
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index("ix_media_files_oshash", "media_files", ["oshash"])


def downgrade() -> None:
    op.drop_index("ix_media_files_oshash", table_name="media_files")
