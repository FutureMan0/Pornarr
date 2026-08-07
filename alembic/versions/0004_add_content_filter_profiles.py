"""Add content-filter profiles and rules.

Revision ID: 0004
Revises: 0003
Create Date: 2026-08-07
"""

from __future__ import annotations

from collections.abc import Sequence
from uuid import uuid4

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

filter_profile_scope = postgresql.ENUM(
    "global", "user", name="filter_profile_scope", create_type=False
)
filter_rule_kind = postgresql.ENUM(
    "term",
    "tag",
    "performer",
    "minimum_confidence",
    "unknown_performer_age",
    "unknown_file_type",
    name="filter_rule_kind",
    create_type=False,
)
filter_action = postgresql.ENUM(
    "allow", "quarantine", "reject", name="filter_action", create_type=False
)


def upgrade() -> None:
    filter_profile_scope.create(op.get_bind(), checkfirst=True)
    filter_rule_kind.create(op.get_bind(), checkfirst=True)
    filter_action.create(op.get_bind(), checkfirst=True)
    op.create_table(
        "content_filter_profiles",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("scope", filter_profile_scope, nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=True),
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
        sa.CheckConstraint(
            "(scope = 'global' AND user_id IS NULL) OR (scope = 'user' AND user_id IS NOT NULL)",
            name=op.f("ck_content_filter_profiles_scope_owner"),
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_content_filter_profiles_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_content_filter_profiles")),
        sa.UniqueConstraint("user_id", name=op.f("uq_content_filter_profiles_user_id")),
    )
    op.create_index(
        "uq_content_filter_profiles_global_scope",
        "content_filter_profiles",
        ["scope"],
        unique=True,
        postgresql_where=sa.text("scope = 'global'"),
    )
    op.create_table(
        "content_filter_rules",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("profile_id", sa.Uuid(), nullable=False),
        sa.Column("kind", filter_rule_kind, nullable=False),
        sa.Column("pattern", sa.String(length=512), server_default="", nullable=False),
        sa.Column("action", filter_action, server_default="reject", nullable=False),
        sa.Column("enabled", sa.Boolean(), server_default="false", nullable=False),
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
            ["profile_id"],
            ["content_filter_profiles.id"],
            name=op.f("fk_content_filter_rules_profile_id_content_filter_profiles"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_content_filter_rules")),
    )

    profile_id = uuid4()
    profile_table = sa.table(
        "content_filter_profiles",
        sa.column("id", sa.Uuid()),
        sa.column("scope", filter_profile_scope),
    )
    rule_table = sa.table(
        "content_filter_rules",
        sa.column("id", sa.Uuid()),
        sa.column("profile_id", sa.Uuid()),
        sa.column("kind", filter_rule_kind),
        sa.column("pattern", sa.String()),
        sa.column("action", filter_action),
        sa.column("enabled", sa.Boolean()),
    )
    op.bulk_insert(profile_table, [{"id": profile_id, "scope": "global"}])
    op.bulk_insert(
        rule_table,
        [
            {
                "id": uuid4(),
                "profile_id": profile_id,
                "kind": kind,
                "pattern": "",
                "action": "reject",
                "enabled": False,
            }
            for kind in filter_rule_kind.enums
        ],
    )


def downgrade() -> None:
    op.drop_table("content_filter_rules")
    op.drop_index("uq_content_filter_profiles_global_scope", table_name="content_filter_profiles")
    op.drop_table("content_filter_profiles")
    filter_action.drop(op.get_bind(), checkfirst=True)
    filter_rule_kind.drop(op.get_bind(), checkfirst=True)
    filter_profile_scope.drop(op.get_bind(), checkfirst=True)
