"""Store technical media-probe metadata.

Revision ID: 0008
Revises: 0007
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
    op.add_column("media_files", sa.Column("duration_seconds", sa.Float(), nullable=True))
    op.add_column("media_files", sa.Column("bitrate", sa.BigInteger(), nullable=True))
    op.add_column("media_files", sa.Column("streams", sa.JSON(), nullable=True))
    op.add_column("media_files", sa.Column("container", sa.String(length=128), nullable=True))


def downgrade() -> None:
    op.drop_column("media_files", "container")
    op.drop_column("media_files", "streams")
    op.drop_column("media_files", "bitrate")
    op.drop_column("media_files", "duration_seconds")
