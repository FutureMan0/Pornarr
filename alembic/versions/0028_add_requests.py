"""Add request lifecycle and status history.

Revision ID: 0028
Revises: 0027
Create Date: 2026-08-08
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0028"
down_revision: str | None = "0027"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

request_status = postgresql.ENUM(
    "searching",
    "results_found",
    "queued",
    "downloading",
    "processing",
    "available",
    "failed",
    "not_found",
    "monitoring",
    "cancelled",
    name="request_status",
    create_type=False,
)


def upgrade() -> None:
    request_status.create(op.get_bind(), checkfirst=True)
    op.create_table(
        "requests",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("query", sa.String(length=512), nullable=False),
        sa.Column("selected_release_guid", sa.String(length=1024), nullable=True),
        sa.Column("status", request_status, nullable=False),
        sa.Column("priority", sa.Integer(), server_default="50", nullable=False),
        sa.Column("download_job_id", sa.Uuid(), nullable=True),
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
            ["user_id"], ["users.id"], name=op.f("fk_requests_user_id_users"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["download_job_id"],
            ["download_jobs.id"],
            name=op.f("fk_requests_download_job_id_download_jobs"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_requests")),
    )
    op.create_table(
        "request_history",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("request_id", sa.Uuid(), nullable=False),
        sa.Column("status", request_status, nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["request_id"],
            ["requests.id"],
            name=op.f("fk_request_history_request_id_requests"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_request_history")),
    )


def downgrade() -> None:
    op.drop_table("request_history")
    op.drop_table("requests")
    request_status.drop(op.get_bind(), checkfirst=True)
