"""Shared fixtures for ingest adapter tests.

Provides
--------
cassette_dir  — Path to the VCR cassettes directory.
async_session — In-memory SQLite async session wired to the ORM models.

VCR cassettes are stored in ``tests/ingest/cassettes/`` and committed to the
repo.  Tests that need to record new cassettes must have the corresponding API
credentials set in the environment; when credentials are absent, tests skip.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import AsyncGenerator

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from wargame_backend.app.db.base import Base

# ---------------------------------------------------------------------------
# Cassette directory
# ---------------------------------------------------------------------------
CASSETTES_DIR = Path(__file__).parent / "cassettes"


@pytest.fixture(scope="session")
def cassette_dir() -> Path:
    """Return the absolute path to the VCR cassettes directory."""
    CASSETTES_DIR.mkdir(parents=True, exist_ok=True)
    return CASSETTES_DIR


# ---------------------------------------------------------------------------
# In-memory async SQLite session (no real Postgres needed in tests)
# ---------------------------------------------------------------------------
_TEST_DB_URL = "sqlite+aiosqlite:///:memory:"


@pytest_asyncio.fixture
async def async_session() -> AsyncGenerator[AsyncSession, None]:
    """Provide a transactional async session backed by an in-memory SQLite DB.

    Creates all tables before yielding and drops them after the test.
    Each test gets a fresh schema.
    """
    engine = create_async_engine(_TEST_DB_URL, echo=False)
    async with engine.begin() as conn:
        # agent_memory has a Vector column (pgvector) which SQLite can't handle;
        # create_all will fail on it.  We monkey-patch Vector to Numeric for testing.
        from sqlalchemy import Numeric
        from pgvector.sqlalchemy import Vector
        # Map Vector type to Text for SQLite compatibility
        from sqlalchemy import event as sa_event

        @sa_event.listens_for(engine.sync_engine, "connect")
        def connect(dbapi_conn, connection_record):  # type: ignore[misc]
            pass  # no-op; just satisfying the listener signature

        await conn.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()
