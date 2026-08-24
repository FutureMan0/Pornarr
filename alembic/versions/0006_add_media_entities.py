"""Add performers, studios, tags and media assignments.

Revision ID: 0006
Revises: 0005
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _entity_table(name: str) -> None:
    op.create_table(
        name,
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=256), nullable=False),
        sa.Column("normalized_name", sa.String(length=256), nullable=False),
        sa.Column("metadata", sa.JSON(), nullable=False),
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
        sa.PrimaryKeyConstraint("id", name=op.f(f"pk_{name}")),
        sa.UniqueConstraint("normalized_name", name=op.f(f"uq_{name}_normalized_name")),
    )
    op.create_index(
        f"ix_{name}_normalized_name_trgm",
        name,
        ["normalized_name"],
        postgresql_using="gin",
        postgresql_ops={"normalized_name": "gin_trgm_ops"},
    )


def upgrade() -> None:
    for table in ("performers", "studios", "tags"):
        _entity_table(table)
    op.create_table(
        "media_performers",
        sa.Column("media_id", sa.Uuid(), nullable=False),
        sa.Column("performer_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["media_id"],
            ["media.id"],
            name=op.f("fk_media_performers_media_id_media"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["performer_id"],
            ["performers.id"],
            name=op.f("fk_media_performers_performer_id_performers"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("media_id", "performer_id", name=op.f("pk_media_performers")),
    )
    op.create_table(
        "media_tags",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("media_id", sa.Uuid(), nullable=False),
        sa.Column("tag_id", sa.Uuid(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("source", sa.String(length=64), nullable=False),
        sa.Column("model_version", sa.String(length=64), nullable=True),
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
            ["media_id"],
            ["media.id"],
            name=op.f("fk_media_tags_media_id_media"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tag_id"], ["tags.id"], name=op.f("fk_media_tags_tag_id_tags"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_media_tags")),
    )
    op.create_index(
        "uq_media_tags_source", "media_tags", ["media_id", "tag_id", "source"], unique=True
    )


def downgrade() -> None:
    op.drop_table("media_tags")
    op.drop_table("media_performers")
    for table in ("tags", "studios", "performers"):
        op.drop_table(table)
