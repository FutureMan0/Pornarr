"""Add OIDC provider configuration.

Revision ID: 0003
Revises: 0002
Create Date: 2026-08-07
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "oidc_providers",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("issuer", sa.String(length=512), nullable=False),
        sa.Column("client_id", sa.String(length=512), nullable=False),
        sa.Column("client_secret", sa.String(length=1024), nullable=False),
        sa.Column("scopes", sa.JSON(), nullable=False),
        sa.Column("enabled", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("discovery_document", sa.JSON(), nullable=True),
        sa.Column("discovery_fetched_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.PrimaryKeyConstraint("id", name=op.f("pk_oidc_providers")),
        sa.UniqueConstraint("name", name=op.f("uq_oidc_providers_name")),
        sa.UniqueConstraint("issuer", name=op.f("uq_oidc_providers_issuer")),
    )


def downgrade() -> None:
    op.drop_table("oidc_providers")
