"""Take the server default off `indexers.search_categories`.

Revision ID: 0053
Revises: 0052
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0053"
down_revision: str | None = "0052"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """0051 wrote a server default the model does not declare.

    `alembic check` reported the difference on every run - the only drift
    between the schema and the models, and the one thing the integration suite
    found when it was finally run. Every other JSON column in this schema
    leaves the default to the ORM, and the ORM always supplies this one:
    `Indexer.search_categories` has `default=lambda: ["6000"]`, so a row is
    never written without it. Taking the default off the column is therefore
    the side that changes nothing at runtime, and it is the side that matches
    the house style.

    The declared cast cannot simply move to the model: `'["6000"]'::json` is
    PostgreSQL's, and the unit suite builds these tables in SQLite.
    """

    op.alter_column("indexers", "search_categories", server_default=None)


def downgrade() -> None:
    op.alter_column(
        "indexers",
        "search_categories",
        server_default=sa.text("'[\"6000\"]'::json"),
    )
