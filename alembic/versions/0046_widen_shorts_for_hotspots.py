"""Allow five-minute shorts and mark the automatically cut ones.

Revision ID: 0046
Revises: 0045
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0046"
down_revision: str | None = "0045"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OLD_LIMIT = 60
NEW_LIMIT = 300


def upgrade() -> None:
    op.drop_constraint(op.f("ck_shorts_under_a_minute"), "shorts", type_="check")
    op.create_check_constraint(
        "within_clip_length", "shorts", f"end_seconds - start_seconds <= {NEW_LIMIT}"
    )
    # ALTER TYPE ... ADD VALUE cannot run inside a transaction block on older
    # servers and cannot be reversed at all, so the enum is widened by rebuild:
    # rename the old type, create the new one, migrate the column, drop the old.
    # It is the only form of this change that has a working downgrade.
    _replace_short_source(("marker", "manual", "hotspot"))


def downgrade() -> None:
    # Automatic clips have no meaning without the value that identifies them,
    # and a five-minute clip violates the constraint being restored.
    op.execute("DELETE FROM shorts WHERE source = 'hotspot'")
    op.execute(f"DELETE FROM shorts WHERE end_seconds - start_seconds > {OLD_LIMIT}")
    _replace_short_source(("marker", "manual"))
    op.drop_constraint(op.f("ck_shorts_within_clip_length"), "shorts", type_="check")
    op.create_check_constraint(
        "under_a_minute", "shorts", f"end_seconds - start_seconds <= {OLD_LIMIT}"
    )


def _replace_short_source(new: tuple[str, ...]) -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    new_values = ", ".join(f"'{value}'" for value in new)
    op.execute("ALTER TYPE short_source RENAME TO short_source_old")
    op.execute(f"CREATE TYPE short_source AS ENUM ({new_values})")
    op.execute(
        "ALTER TABLE shorts ALTER COLUMN source DROP DEFAULT, "
        "ALTER COLUMN source TYPE short_source USING source::text::short_source, "
        "ALTER COLUMN source SET DEFAULT 'manual'"
    )
    op.execute("DROP TYPE short_source_old")
