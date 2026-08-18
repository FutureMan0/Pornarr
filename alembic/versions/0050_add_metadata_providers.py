"""Store the key a scene-metadata provider needs.

Revision ID: 0049
Revises: 0048
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0050"
down_revision: str | None = "0049"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "metadata_providers",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("implementation", sa.String(length=64), nullable=False),
        sa.Column("endpoint", sa.String(length=512), nullable=True),
        sa.Column("api_key", sa.String(length=1024), nullable=False),
        sa.Column("priority", sa.Integer(), server_default="0", nullable=False),
        sa.Column("enabled", sa.Boolean(), server_default="true", nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_metadata_providers")),
        sa.UniqueConstraint("implementation", name=op.f("uq_metadata_providers_implementation")),
    )


def downgrade() -> None:
    op.drop_table("metadata_providers")
