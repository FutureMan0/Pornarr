"""Enable pg_trgm

Trigram similarity carries local search, fuzzy title matching and duplicate
detection. It is enabled in the first migration deliberately: creating an
extension requires elevated privileges, and discovering that halfway through a
release is worse than discovering it on first install.

Revision ID: 0001
Revises:
Create Date: 2026-08-07
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")


def downgrade() -> None:
    # Dropping the extension would drop every index built on it. Downgrades that
    # destroy data the migration did not create are a trap, so this one does not.
    pass
