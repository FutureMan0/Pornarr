"""Add custom formats and their conditions.

Revision ID: 0008
Revises: 0007
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

custom_format_field = postgresql.ENUM(
    "title",
    "codec",
    "source",
    "size",
    "indexer",
    "protocol",
    "flags",
    name="custom_format_field",
    create_type=False,
)
custom_format_operator = postgresql.ENUM(
    "equals",
    "contains",
    "greater_than",
    "less_than",
    name="custom_format_operator",
    create_type=False,
)


def upgrade() -> None:
    custom_format_field.create(op.get_bind(), checkfirst=True)
    custom_format_operator.create(op.get_bind(), checkfirst=True)
    op.create_table(
        "custom_formats",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("score", sa.Integer(), server_default="0", nullable=False),
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
        sa.PrimaryKeyConstraint("id", name=op.f("pk_custom_formats")),
        sa.UniqueConstraint("name", name=op.f("uq_custom_formats_name")),
    )
    op.create_table(
        "custom_format_conditions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("custom_format_id", sa.Uuid(), nullable=False),
        sa.Column("field", custom_format_field, nullable=False),
        sa.Column("operator", custom_format_operator, nullable=False),
        sa.Column("value", sa.JSON(), nullable=False),
        sa.Column("negate", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("required", sa.Boolean(), server_default="true", nullable=False),
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
        sa.ForeignKeyConstraint(
            ["custom_format_id"],
            ["custom_formats.id"],
            name=op.f("fk_custom_format_conditions_custom_format_id_custom_formats"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_custom_format_conditions")),
    )


def downgrade() -> None:
    op.drop_table("custom_format_conditions")
    op.drop_table("custom_formats")
    custom_format_operator.drop(op.get_bind(), checkfirst=True)
    custom_format_field.drop(op.get_bind(), checkfirst=True)
