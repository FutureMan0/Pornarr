"""Add persistent download jobs, history and retry blocks.

Revision ID: 0013
Revises: 0012
Create Date: 2026-08-08
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0013"
down_revision: str | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "download_jobs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("download_client_id", sa.Uuid(), nullable=True),
        sa.Column("client_name", sa.String(length=128), nullable=False),
        sa.Column("protocol", sa.String(length=32), nullable=False),
        sa.Column("release_guid", sa.String(length=1024), nullable=False),
        sa.Column("client_job_id", sa.String(length=512), nullable=True),
        sa.Column("status", sa.String(length=32), server_default="queued", nullable=False),
        sa.Column("priority", sa.Integer(), server_default="0", nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=True),
        sa.Column("remaining_bytes", sa.BigInteger(), nullable=True),
        sa.Column("download_speed_bytes", sa.BigInteger(), nullable=True),
        sa.Column("estimated_seconds", sa.Integer(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
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
            ["download_client_id"],
            ["download_clients.id"],
            name=op.f("fk_download_jobs_download_client_id_download_clients"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_download_jobs")),
        sa.UniqueConstraint(
            "download_client_id",
            "client_job_id",
            name=op.f("uq_download_jobs_download_client_id_client_job_id"),
        ),
    )
    op.create_index("ix_download_jobs_queue", "download_jobs", ["status", "priority"], unique=False)

    op.create_table(
        "download_history",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("download_job_id", sa.Uuid(), nullable=True),
        sa.Column("client_name", sa.String(length=128), nullable=False),
        sa.Column("client_job_id", sa.String(length=512), nullable=True),
        sa.Column("protocol", sa.String(length=32), nullable=False),
        sa.Column("release_guid", sa.String(length=1024), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["download_job_id"],
            ["download_jobs.id"],
            name=op.f("fk_download_history_download_job_id_download_jobs"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_download_history")),
        sa.UniqueConstraint("download_job_id", name=op.f("uq_download_history_download_job_id")),
    )

    op.create_table(
        "blocked_releases",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("release_guid", sa.String(length=1024), nullable=False),
        sa.Column("blocked_until", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
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
        sa.PrimaryKeyConstraint("id", name=op.f("pk_blocked_releases")),
        sa.UniqueConstraint("release_guid", name=op.f("uq_blocked_releases_release_guid")),
    )


def downgrade() -> None:
    op.drop_table("blocked_releases")
    op.drop_table("download_history")
    op.drop_index("ix_download_jobs_queue", table_name="download_jobs")
    op.drop_table("download_jobs")
