"""Add invitations.

Revision ID: 0048
Revises: 0047
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0048"
down_revision = "0047"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "invites",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        # The token itself is never stored; this is a SHA-256 of it. Unique so a
        # presented token is one index lookup rather than a scan.
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "created_by",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("redeemed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "redeemed_by",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("note", sa.String(length=128), nullable=True),
        # Server defaults, matching `TimestampMixin`. Without them the model
        # inserts nothing for these columns and PostgreSQL refuses the row —
        # invisible to the unit suite, which builds its schema from the models
        # rather than from this file.
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
        # A constraint, not a unique index. `unique=True` on the model's column
        # is a UniqueConstraint to SQLAlchemy, and `alembic check` reports the
        # difference as drift — the two are equivalent in PostgreSQL and not
        # equivalent to the autogenerate comparison.
        sa.UniqueConstraint("token_hash", name=op.f("uq_invites_token_hash")),
    )


def downgrade() -> None:
    op.drop_table("invites")
