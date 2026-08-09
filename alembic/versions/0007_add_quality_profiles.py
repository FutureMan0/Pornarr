"""Add quality definitions and ordered profiles.

Revision ID: 0007
Revises: 0006
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_QUALITY_720P = "3a18d9ca-d623-4b8c-9a00-000000000001"
_QUALITY_1080P = "3a18d9ca-d623-4b8c-9a00-000000000002"
_QUALITY_2160P = "3a18d9ca-d623-4b8c-9a00-000000000003"
_DEFAULT_PROFILE = "3a18d9ca-d623-4b8c-9a00-000000000010"


def upgrade() -> None:
    op.create_table(
        "quality_definitions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("resolution", sa.String(length=32), nullable=False),
        sa.Column("source", sa.String(length=64), nullable=False),
        sa.Column("weight", sa.Integer(), nullable=False),
        sa.Column("minimum_size_mb_per_minute", sa.Float(), nullable=False),
        sa.Column("maximum_size_mb_per_minute", sa.Float(), nullable=False),
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
        sa.CheckConstraint("weight >= 0", name=op.f("ck_quality_definitions_weight_not_negative")),
        sa.CheckConstraint(
            "minimum_size_mb_per_minute >= 0",
            name=op.f("ck_quality_definitions_minimum_size_not_negative"),
        ),
        sa.CheckConstraint(
            "maximum_size_mb_per_minute >= minimum_size_mb_per_minute",
            name=op.f("ck_quality_definitions_size_bounds_ordered"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_quality_definitions")),
        sa.UniqueConstraint("name", name=op.f("uq_quality_definitions_name")),
    )
    op.create_table(
        "quality_profiles",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("cutoff_quality_id", sa.Uuid(), nullable=False),
        sa.Column("minimum_custom_format_score", sa.Integer(), server_default="0", nullable=False),
        sa.Column("is_default", sa.Boolean(), server_default="false", nullable=False),
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
            ["cutoff_quality_id"],
            ["quality_definitions.id"],
            name=op.f("fk_quality_profiles_cutoff_quality_id_quality_definitions"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_quality_profiles")),
        sa.UniqueConstraint("name", name=op.f("uq_quality_profiles_name")),
    )
    op.create_index(
        "uq_quality_profiles_default",
        "quality_profiles",
        ["is_default"],
        unique=True,
        postgresql_where=sa.text("is_default"),
    )
    op.create_table(
        "quality_profile_items",
        sa.Column("profile_id", sa.Uuid(), nullable=False),
        sa.Column("quality_definition_id", sa.Uuid(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.CheckConstraint(
            "position >= 0", name=op.f("ck_quality_profile_items_position_not_negative")
        ),
        sa.ForeignKeyConstraint(
            ["profile_id"],
            ["quality_profiles.id"],
            name=op.f("fk_quality_profile_items_profile_id_quality_profiles"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["quality_definition_id"],
            ["quality_definitions.id"],
            name=op.f("fk_quality_profile_items_quality_definition_id_quality_definitions"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint(
            "profile_id", "quality_definition_id", name=op.f("pk_quality_profile_items")
        ),
        sa.UniqueConstraint(
            "profile_id", "position", name=op.f("uq_quality_profile_items_profile_id_position")
        ),
    )

    definitions = sa.table(
        "quality_definitions",
        sa.column("id", sa.Uuid()),
        sa.column("name", sa.String()),
        sa.column("resolution", sa.String()),
        sa.column("source", sa.String()),
        sa.column("weight", sa.Integer()),
        sa.column("minimum_size_mb_per_minute", sa.Float()),
        sa.column("maximum_size_mb_per_minute", sa.Float()),
    )
    op.bulk_insert(
        definitions,
        [
            {
                "id": _QUALITY_720P,
                "name": "WEB 720p",
                "resolution": "720p",
                "source": "web",
                "weight": 10,
                "minimum_size_mb_per_minute": 5,
                "maximum_size_mb_per_minute": 20,
            },
            {
                "id": _QUALITY_1080P,
                "name": "WEB 1080p",
                "resolution": "1080p",
                "source": "web",
                "weight": 20,
                "minimum_size_mb_per_minute": 10,
                "maximum_size_mb_per_minute": 35,
            },
            {
                "id": _QUALITY_2160P,
                "name": "WEB 2160p",
                "resolution": "2160p",
                "source": "web",
                "weight": 30,
                "minimum_size_mb_per_minute": 15,
                "maximum_size_mb_per_minute": 80,
            },
        ],
    )
    profiles = sa.table(
        "quality_profiles",
        sa.column("id", sa.Uuid()),
        sa.column("name", sa.String()),
        sa.column("cutoff_quality_id", sa.Uuid()),
        sa.column("minimum_custom_format_score", sa.Integer()),
        sa.column("is_default", sa.Boolean()),
    )
    op.bulk_insert(
        profiles,
        [
            {
                "id": _DEFAULT_PROFILE,
                "name": "Default",
                "cutoff_quality_id": _QUALITY_2160P,
                "minimum_custom_format_score": 0,
                "is_default": True,
            }
        ],
    )
    items = sa.table(
        "quality_profile_items",
        sa.column("profile_id", sa.Uuid()),
        sa.column("quality_definition_id", sa.Uuid()),
        sa.column("position", sa.Integer()),
    )
    op.bulk_insert(
        items,
        [
            {"profile_id": _DEFAULT_PROFILE, "quality_definition_id": _QUALITY_720P, "position": 0},
            {
                "profile_id": _DEFAULT_PROFILE,
                "quality_definition_id": _QUALITY_1080P,
                "position": 1,
            },
            {
                "profile_id": _DEFAULT_PROFILE,
                "quality_definition_id": _QUALITY_2160P,
                "position": 2,
            },
        ],
    )


def downgrade() -> None:
    op.drop_table("quality_profile_items")
    op.drop_index("uq_quality_profiles_default", table_name="quality_profiles")
    op.drop_table("quality_profiles")
    op.drop_table("quality_definitions")
