"""Add OIDC claim mapping.

Revision ID: 0025
Revises: 0024
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0025"
down_revision: str | None = "0024"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "oidc_providers",
        sa.Column(
            "username_claim",
            sa.String(length=128),
            server_default="preferred_username",
            nullable=False,
        ),
    )
    op.add_column(
        "oidc_providers",
        sa.Column("role_claim", sa.String(length=128), server_default="groups", nullable=False),
    )
    op.add_column(
        "oidc_providers",
        sa.Column("role_mapping", sa.JSON(), server_default=sa.text("'{}'"), nullable=False),
    )
    op.add_column(
        "oidc_providers",
        sa.Column("default_role", sa.String(length=16), server_default="user", nullable=False),
    )
    op.add_column(
        "oidc_providers", sa.Column("required_claim", sa.String(length=128), nullable=True)
    )
    op.add_column(
        "oidc_providers", sa.Column("required_claim_value", sa.String(length=512), nullable=True)
    )
    op.alter_column("oidc_providers", "role_mapping", server_default=None)


def downgrade() -> None:
    op.drop_column("oidc_providers", "required_claim_value")
    op.drop_column("oidc_providers", "required_claim")
    op.drop_column("oidc_providers", "default_role")
    op.drop_column("oidc_providers", "role_mapping")
    op.drop_column("oidc_providers", "role_claim")
    op.drop_column("oidc_providers", "username_claim")
