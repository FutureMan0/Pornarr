"""Add ratings, comments, shorts, sends and per-guest collections.

Revision ID: 0044
Revises: 0043
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0044"
down_revision: str | None = "0043"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Declared with `create_type=False` and created explicitly: autogenerate emits
# an inline `sa.Enum`, which PostgreSQL creates as a side effect of the first
# table but never drops on downgrade. The second upgrade then fails on
# `CREATE TYPE ... already exists`.
collection_visibility = postgresql.ENUM(
    "private", "shared", name="collection_visibility", create_type=False
)
comment_state = postgresql.ENUM(
    "open", "answered", "hidden", name="comment_state", create_type=False
)
short_source = postgresql.ENUM("marker", "manual", name="short_source", create_type=False)


def upgrade() -> None:
    collection_visibility.create(op.get_bind(), checkfirst=True)
    comment_state.create(op.get_bind(), checkfirst=True)
    short_source.create(op.get_bind(), checkfirst=True)
    op.create_table(
        "collections",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column(
            "visibility",
            collection_visibility,
            server_default="private",
            nullable=False,
        ),
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
        sa.CheckConstraint("length(trim(name)) > 0", name=op.f("ck_collections_name_not_blank")),
        sa.ForeignKeyConstraint(
            ["owner_id"],
            ["users.id"],
            name=op.f("fk_collections_owner_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_collections")),
        sa.UniqueConstraint("owner_id", "name", name=op.f("uq_collections_owner_id_name")),
    )
    op.create_index(op.f("ix_collections_owner_id"), "collections", ["owner_id"], unique=False)
    op.create_table(
        "comments",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("media_id", sa.Uuid(), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column(
            "state",
            comment_state,
            server_default="open",
            nullable=False,
        ),
        sa.Column("edited_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.CheckConstraint("length(trim(body)) > 0", name=op.f("ck_comments_body_not_blank")),
        sa.ForeignKeyConstraint(
            ["media_id"], ["media.id"], name=op.f("fk_comments_media_id_media"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_comments_user_id_users"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_comments")),
    )
    op.create_index(
        "ix_comments_media_id_created_at", "comments", ["media_id", "created_at"], unique=False
    )
    op.create_table(
        "media_sends",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("sender_id", sa.Uuid(), nullable=False),
        sa.Column("recipient_id", sa.Uuid(), nullable=False),
        sa.Column("media_id", sa.Uuid(), nullable=False),
        sa.Column("note", sa.String(length=1024), nullable=True),
        sa.Column("seen_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.CheckConstraint("sender_id <> recipient_id", name=op.f("ck_media_sends_no_self_send")),
        sa.ForeignKeyConstraint(
            ["media_id"],
            ["media.id"],
            name=op.f("fk_media_sends_media_id_media"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["recipient_id"],
            ["users.id"],
            name=op.f("fk_media_sends_recipient_id_users"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["sender_id"],
            ["users.id"],
            name=op.f("fk_media_sends_sender_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_media_sends")),
        sa.UniqueConstraint(
            "sender_id",
            "recipient_id",
            "media_id",
            name=op.f("uq_media_sends_sender_id_recipient_id_media_id"),
        ),
    )
    op.create_index(
        op.f("ix_media_sends_recipient_id"), "media_sends", ["recipient_id"], unique=False
    )
    op.create_table(
        "ratings",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("media_id", sa.Uuid(), nullable=False),
        sa.Column("stars", sa.Integer(), nullable=False),
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
        sa.CheckConstraint("stars BETWEEN 1 AND 5", name=op.f("ck_ratings_stars_within_scale")),
        sa.ForeignKeyConstraint(
            ["media_id"], ["media.id"], name=op.f("fk_ratings_media_id_media"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_ratings_user_id_users"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ratings")),
        sa.UniqueConstraint("user_id", "media_id", name=op.f("uq_ratings_user_id_media_id")),
    )
    op.create_index(op.f("ix_ratings_media_id"), "ratings", ["media_id"], unique=False)
    op.create_table(
        "shorts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("media_id", sa.Uuid(), nullable=False),
        sa.Column("title", sa.String(length=512), nullable=False),
        sa.Column("start_seconds", sa.Float(), nullable=False),
        sa.Column("end_seconds", sa.Float(), nullable=False),
        sa.Column(
            "source",
            short_source,
            server_default="manual",
            nullable=False,
        ),
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
            "end_seconds - start_seconds <= 60.0", name=op.f("ck_shorts_under_a_minute")
        ),
        sa.CheckConstraint("end_seconds > start_seconds", name=op.f("ck_shorts_end_after_start")),
        sa.CheckConstraint("start_seconds >= 0", name=op.f("ck_shorts_start_not_negative")),
        sa.ForeignKeyConstraint(
            ["media_id"], ["media.id"], name=op.f("fk_shorts_media_id_media"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_shorts")),
        sa.UniqueConstraint(
            "media_id", "start_seconds", name=op.f("uq_shorts_media_id_start_seconds")
        ),
    )
    op.create_index(op.f("ix_shorts_media_id"), "shorts", ["media_id"], unique=False)
    op.create_table(
        "collection_items",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("collection_id", sa.Uuid(), nullable=False),
        sa.Column("media_id", sa.Uuid(), nullable=False),
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
            ["collection_id"],
            ["collections.id"],
            name=op.f("fk_collection_items_collection_id_collections"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["media_id"],
            ["media.id"],
            name=op.f("fk_collection_items_media_id_media"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_collection_items")),
        sa.UniqueConstraint(
            "collection_id", "media_id", name=op.f("uq_collection_items_collection_id_media_id")
        ),
    )
    op.create_index(
        op.f("ix_collection_items_collection_id"),
        "collection_items",
        ["collection_id"],
        unique=False,
    )
    op.create_table(
        "comment_likes",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("comment_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
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
            ["comment_id"],
            ["comments.id"],
            name=op.f("fk_comment_likes_comment_id_comments"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_comment_likes_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_comment_likes")),
        sa.UniqueConstraint(
            "comment_id", "user_id", name=op.f("uq_comment_likes_comment_id_user_id")
        ),
    )
    op.create_index(
        op.f("ix_comment_likes_comment_id"), "comment_likes", ["comment_id"], unique=False
    )
    op.create_table(
        "comment_reports",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("comment_id", sa.Uuid(), nullable=False),
        sa.Column("reporter_id", sa.Uuid(), nullable=False),
        sa.Column("reason", sa.String(length=512), nullable=True),
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
            ["comment_id"],
            ["comments.id"],
            name=op.f("fk_comment_reports_comment_id_comments"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["reporter_id"],
            ["users.id"],
            name=op.f("fk_comment_reports_reporter_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_comment_reports")),
        sa.UniqueConstraint(
            "comment_id", "reporter_id", name=op.f("uq_comment_reports_comment_id_reporter_id")
        ),
    )
    op.create_index(
        op.f("ix_comment_reports_comment_id"), "comment_reports", ["comment_id"], unique=False
    )
    op.add_column("users", sa.Column("display_name", sa.String(length=64), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "display_name")
    op.drop_index(op.f("ix_comment_reports_comment_id"), table_name="comment_reports")
    op.drop_table("comment_reports")
    op.drop_index(op.f("ix_comment_likes_comment_id"), table_name="comment_likes")
    op.drop_table("comment_likes")
    op.drop_index(op.f("ix_collection_items_collection_id"), table_name="collection_items")
    op.drop_table("collection_items")
    op.drop_index(op.f("ix_shorts_media_id"), table_name="shorts")
    op.drop_table("shorts")
    op.drop_index(op.f("ix_ratings_media_id"), table_name="ratings")
    op.drop_table("ratings")
    op.drop_index(op.f("ix_media_sends_recipient_id"), table_name="media_sends")
    op.drop_table("media_sends")
    op.drop_index("ix_comments_media_id_created_at", table_name="comments")
    op.drop_table("comments")
    op.drop_index(op.f("ix_collections_owner_id"), table_name="collections")
    op.drop_table("collections")
    short_source.drop(op.get_bind(), checkfirst=True)
    comment_state.drop(op.get_bind(), checkfirst=True)
    collection_visibility.drop(op.get_bind(), checkfirst=True)
