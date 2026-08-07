"""Alembic environment.

The database URL comes from application settings rather than from alembic.ini, so
there is exactly one place that knows how to reach the database and no credential
is committed.
"""

from __future__ import annotations

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from pornarr_db import models  # noqa: F401 - registers models on the metadata
from pornarr_db.base import Base
from pornarr_shared.config import get_settings

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

_EXTENSION_TABLES = frozenset({"pg_stat_statements"})

target_metadata = Base.metadata

config.set_main_option("sqlalchemy.url", get_settings().database_url)


def _include_object(
    obj: object, name: str | None, type_: str, reflected: bool, compare_to: object
) -> bool:
    """Keep extension-owned objects out of autogenerate.

    `pg_trgm` installs functions and operator classes. Without this, autogenerate
    proposes dropping them on every run.
    """
    return not (type_ == "table" and name in _EXTENSION_TABLES)


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        compare_server_default=True,
        include_object=_include_object,
    )
    with context.begin_transaction():
        context.run_migrations()


def _run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        compare_server_default=True,
        include_object=_include_object,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(_run_migrations)
    await connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
