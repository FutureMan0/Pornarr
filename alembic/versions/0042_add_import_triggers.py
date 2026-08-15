"""Persist completed-download handovers before queueing import work.

Revision ID: 0042
Revises: 0041
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0042"
down_revision: str | None = "0041"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "import_triggers",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("download_job_id", sa.Uuid(), nullable=True),
        sa.Column("source_path", sa.String(length=1024), nullable=False),
        sa.Column("reported_path", sa.String(length=1024), nullable=True),
        sa.Column("status", sa.String(length=32), server_default="pending", nullable=False),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("error_detail", sa.Text(), nullable=True),
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
        sa.ForeignKeyConstraint(["download_job_id"], ["download_jobs.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_import_triggers")),
        sa.UniqueConstraint("download_job_id", name=op.f("uq_import_triggers_download_job_id")),
        sa.UniqueConstraint("source_path", name=op.f("uq_import_triggers_source_path")),
    )
    op.create_index("ix_import_triggers_status", "import_triggers", ["status"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_import_triggers_status", table_name="import_triggers")
    op.drop_table("import_triggers")
