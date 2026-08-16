"""Add rolling performance measurements.

Revision ID: 0023
Revises: 0022
Create Date: 2026-08-08
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0023"
down_revision: str | None = "0022"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

PERFORMANCE_METRIC = sa.Enum(
    "download_speed",
    "post_processing_seconds_per_gib",
    "import_seconds_per_gib",
    "disk_write_speed",
    name="performance_metric",
)


def upgrade() -> None:
    op.create_table(
        "performance_measurements",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("metric", PERFORMANCE_METRIC, nullable=False),
        sa.Column("scope", sa.String(length=32), nullable=True),
        sa.Column("value", sa.Float(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_performance_measurements")),
    )
    op.create_index(
        "ix_performance_measurements_metric_scope_created_at",
        "performance_measurements",
        ["metric", "scope", "created_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_performance_measurements_metric_scope_created_at",
        table_name="performance_measurements",
    )
    op.drop_table("performance_measurements")
    PERFORMANCE_METRIC.drop(op.get_bind(), checkfirst=True)
