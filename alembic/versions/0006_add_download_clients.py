"""Add configured download clients.

Revision ID: 0012
Revises: 0011
Create Date: 2026-08-08

Renumbered from 0006, which by now three separate branches had claimed: this
one, add_indexers and add_media_entities. Each numbered from its own base and
none could see the others. The download-client tables reference nothing in the
chain ahead of them, so appending is the resolution that changes no schema.

Depends on the renumbering in #229 landing first, which is what makes 0011 the
predecessor. The filename still says 0006; alembic reads the identifier below.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "download_clients",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("protocol", sa.String(length=32), nullable=False),
        sa.Column("implementation", sa.String(length=64), nullable=False),
        sa.Column("host", sa.String(length=512), nullable=False),
        sa.Column("port", sa.Integer(), nullable=False),
        sa.Column("url_base", sa.String(length=256), nullable=False),
        sa.Column("credentials", sa.String(length=1024), nullable=False),
        sa.Column("category", sa.String(length=128), nullable=True),
        sa.Column("priority", sa.Integer(), server_default="0", nullable=False),
        sa.Column("remove_completed", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("enabled", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("health", sa.String(length=32), nullable=False),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("last_tested_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.PrimaryKeyConstraint("id", name=op.f("pk_download_clients")),
        sa.UniqueConstraint("name", name=op.f("uq_download_clients_name")),
    )


def downgrade() -> None:
    op.drop_table("download_clients")
