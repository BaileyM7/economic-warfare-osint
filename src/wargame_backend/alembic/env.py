"""Alembic environment configuration — async mode.

Reads the database URL from the DATABASE_URL environment variable via
the application Settings object.  Imports Base.metadata from the models
module so Alembic can auto-generate migrations from ORM definitions.
"""

from __future__ import annotations

import asyncio
import os
import sys
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

# ---------------------------------------------------------------------------
# Make app importable when running `alembic` from src/wargame_backend/.
# Also make the sibling wargame_ai / wargame_shared packages importable by
# adding the Emissary `src/` directory one level up.
# ---------------------------------------------------------------------------
_here = os.path.dirname(os.path.abspath(__file__))
_wargame_backend_dir = os.path.dirname(_here)
_src_dir = os.path.dirname(_wargame_backend_dir)
sys.path.insert(0, _wargame_backend_dir)  # so `from app...` works
sys.path.insert(0, _src_dir)              # so `from wargame_ai/shared...` works

# Import Base (and trigger model registration by importing models)
from wargame_backend.app.db.base import Base  # noqa: E402
import wargame_backend.app.db.models  # noqa: E402, F401 — registers all mappers against Base

# ---------------------------------------------------------------------------
# Alembic Config object (wraps alembic.ini)
# ---------------------------------------------------------------------------
config = context.config

# Override sqlalchemy.url from the environment at runtime.
# Normalize Render-style `postgres://…` to `postgresql+asyncpg://…`
# so the async engine gets the right driver.
_db_url = os.environ.get("DATABASE_URL", "postgresql+asyncpg://swarm:swarm@localhost:5432/swarm")
for _prefix in ("postgres://", "postgresql://"):
    if _db_url.startswith(_prefix) and not _db_url.startswith("postgresql+"):
        _db_url = "postgresql+asyncpg://" + _db_url[len(_prefix):]
        break
config.set_main_option("sqlalchemy.url", _db_url)

# Interpret the config file for Python logging if it has a [loggers] section
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# The metadata object Alembic uses for --autogenerate
target_metadata = Base.metadata


# ---------------------------------------------------------------------------
# Offline mode (generates SQL without a live DB connection)
# ---------------------------------------------------------------------------
def run_migrations_offline() -> None:
    """Render migrations as SQL to stdout."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        compare_server_default=True,
    )

    with context.begin_transaction():
        context.run_migrations()


# ---------------------------------------------------------------------------
# Online mode (runs migrations against a live connection)
# ---------------------------------------------------------------------------
def do_run_migrations(connection: Connection) -> None:
    """Execute migrations in the given synchronous connection context."""
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        compare_server_default=True,
    )

    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """Create an async engine and run migrations."""
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


def run_migrations_online() -> None:
    """Entry point for online mode."""
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
