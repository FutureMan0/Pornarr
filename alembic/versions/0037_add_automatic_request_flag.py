"""Flag automatic requests and reserve a lower default priority for them.

Revision ID: 0037
Revises: 0036
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0037"
down_revision: str | None = "0036"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "requests", sa.Column("is_automatic", sa.Boolean(), server_default="false", nullable=False)
    )
    op.alter_column("requests", "priority", server_default="80")
    op.execute("UPDATE requests SET priority = 80 WHERE priority = 50")


def downgrade() -> None:
    op.alter_column("requests", "priority", server_default="50")
    op.drop_column("requests", "is_automatic")
