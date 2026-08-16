"""Track automatic retry attempts for failed downloads.

Revision ID: 0038
Revises: 0037
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0038"
down_revision: str | None = "0037"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "requests",
        sa.Column("download_retry_attempts", sa.Integer(), server_default="0", nullable=False),
    )


def downgrade() -> None:
    op.drop_column("requests", "download_retry_attempts")
