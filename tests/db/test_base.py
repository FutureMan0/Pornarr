from __future__ import annotations

from datetime import UTC

import pytest
from sqlalchemy import Column, ForeignKey, Integer, String, Table, UniqueConstraint

from pornarr_db.base import NAMING_CONVENTION, Base, TimestampMixin, utcnow


def test_metadata_carries_the_naming_convention() -> None:
    """Unnamed constraints get a database-generated name that autogenerate cannot
    match against the models, producing spurious drop-and-recreate migrations."""
    assert Base.metadata.naming_convention == NAMING_CONVENTION


@pytest.mark.parametrize(
    ("key", "expected_prefix"),
    [("ix", "ix_"), ("uq", "uq_"), ("ck", "ck_"), ("fk", "fk_"), ("pk", "pk_")],
)
def test_every_constraint_kind_has_a_convention(key: str, expected_prefix: str) -> None:
    assert NAMING_CONVENTION[key].startswith(expected_prefix)


def test_convention_produces_deterministic_names() -> None:
    """The point of the convention: a constraint gets the same name whether the
    schema was built from the models or reflected from the database."""
    metadata = Base.metadata
    table = Table(
        "convention_probe",
        metadata,
        Column("id", Integer, primary_key=True),
        Column("owner_id", Integer, ForeignKey("convention_probe.id")),
        Column("name", String(10), index=True),
        UniqueConstraint("name"),
    )
    try:
        assert table.primary_key.name == "pk_convention_probe"

        foreign_key_names = {
            fk.constraint.name for fk in table.foreign_keys if fk.constraint is not None
        }
        assert foreign_key_names == {"fk_convention_probe_owner_id_convention_probe"}

        index_names = {index.name for index in table.indexes}
        assert "ix_convention_probe_name" in index_names

        unique_names = {
            constraint.name
            for constraint in table.constraints
            if isinstance(constraint, UniqueConstraint)
        }
        assert unique_names == {"uq_convention_probe_name"}
    finally:
        metadata.remove(table)


def test_timestamp_mixin_defines_both_columns_server_side() -> None:
    """Set by the database, not by Python: a worker and an API instance do not
    share a clock, and rows written by a migration would have no timestamps."""
    declared = vars(TimestampMixin)

    assert "created_at" in declared
    assert "updated_at" in declared


def test_utcnow_is_timezone_aware() -> None:
    now = utcnow()
    assert now.tzinfo is not None
    assert now.utcoffset() == UTC.utcoffset(None)
