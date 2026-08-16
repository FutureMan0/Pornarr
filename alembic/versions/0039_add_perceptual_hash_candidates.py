"""Store perceptual hashes and non-destructive duplicate candidates.

Revision ID: 0039
Revises: 0038
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0039"
down_revision: str | None = "0038"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("media_files", sa.Column("perceptual_hash", sa.String(length=16), nullable=True))
    op.create_index("ix_media_files_perceptual_hash", "media_files", ["perceptual_hash"])
    op.create_table(
        "duplicate_candidates",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("media_file_id", sa.Uuid(), nullable=False),
        sa.Column("candidate_file_id", sa.Uuid(), nullable=False),
        sa.Column("hamming_distance", sa.Integer(), nullable=False),
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
        sa.ForeignKeyConstraint(["media_file_id"], ["media_files.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["candidate_file_id"], ["media_files.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_duplicate_candidates")),
        sa.UniqueConstraint(
            "media_file_id",
            "candidate_file_id",
            name=op.f("uq_duplicate_candidates_media_file_id_candidate_file_id"),
        ),
    )


def downgrade() -> None:
    op.drop_table("duplicate_candidates")
    op.drop_index("ix_media_files_perceptual_hash", table_name="media_files")
    op.drop_column("media_files", "perceptual_hash")
