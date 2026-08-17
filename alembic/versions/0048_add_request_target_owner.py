"""Record which library an approved request should deposit its media in.

Revision ID: 0048
Revises: 0047
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0048"
down_revision: str | None = "0047"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("requests", sa.Column("target_owner_id", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        op.f("fk_requests_target_owner_id_users"),
        "requests",
        "users",
        ["target_owner_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint(op.f("fk_requests_target_owner_id_users"), "requests", type_="foreignkey")
    op.drop_column("requests", "target_owner_id")
