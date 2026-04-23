"""Pytest configuration and shared fixtures for the Swarm backend test suite.

Database strategy:
  - Uses an in-memory SQLite database via aiosqlite for fast unit tests that
    do not require pgvector.  The ``AgentMemory`` vector column is omitted via
    ``__table_args__`` override (see ``_patch_models`` autouse fixture).
  - To run against a real PostgreSQL instance (e.g. in CI), set the
    ``TEST_DATABASE_URL`` environment variable to a Postgres DSN.
    Run ``alembic upgrade head`` against that DB before the test session.

Redis strategy:
  - Uses ``fakeredis`` (async) for all tests.  No real Redis required.

Usage:
    pytest src/backend/tests/ -v
"""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncGenerator
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import fakeredis.aioredis
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from wargame_backend.app.db.base import Base
from wargame_backend.app.db.models import (  # noqa: F401  — ensure all models are registered
    AgentMemory,
    Country,
    CountryRelationship,
    DataSource,
    Event,
    Scenario,
    SimEvent,
    Simulation,
)
from wargame_backend.app.sim_runner import NullSimRunner


# ---------------------------------------------------------------------------
# Database URL selection
# ---------------------------------------------------------------------------

_TEST_DB_URL = os.environ.get(
    "TEST_DATABASE_URL",
    "sqlite+aiosqlite:///:memory:",
)
_USING_SQLITE = _TEST_DB_URL.startswith("sqlite")


# ---------------------------------------------------------------------------
# Engine + session factory
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture(scope="session")
async def db_engine():
    """Create the async engine once per test session."""
    if _USING_SQLITE:
        engine = create_async_engine(
            _TEST_DB_URL,
            echo=False,
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        # Enable FK support on SQLite
        @event.listens_for(engine.sync_engine, "connect")
        def _set_sqlite_pragma(dbapi_conn, connection_record):  # type: ignore[misc]
            cursor = dbapi_conn.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()
    else:
        engine = create_async_engine(_TEST_DB_URL, echo=False)

    # Create all tables (skips pgvector-specific index creation gracefully on SQLite)
    async with engine.begin() as conn:
        # Patch: skip the HNSW vector index for SQLite
        if _USING_SQLITE:
            # Drop the HNSW index definition from AgentMemory before create_all
            from sqlalchemy import Index

            # Remove incompatible indexes from AgentMemory table args
            table = AgentMemory.__table__
            hnsw_idxs = [
                idx for idx in table.indexes if "hnsw" in idx.name.lower()
            ]
            for idx in hnsw_idxs:
                table.indexes.discard(idx)

        await conn.run_sync(Base.metadata.create_all)

    yield engine

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest_asyncio.fixture
async def db(db_engine) -> AsyncGenerator[AsyncSession, None]:
    """Yield a clean transactional session, rolled back after each test."""
    factory = async_sessionmaker(bind=db_engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as session:
        yield session
        await session.rollback()


# ---------------------------------------------------------------------------
# Fake Redis
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def fake_redis() -> AsyncGenerator[fakeredis.aioredis.FakeRedis, None]:
    """Yield a fake async Redis client (in-memory, no real server needed)."""
    server = fakeredis.FakeServer()
    client = fakeredis.aioredis.FakeRedis(server=server, decode_responses=True)
    yield client
    await client.aclose()


# ---------------------------------------------------------------------------
# SimRunner
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def null_sim_runner(fake_redis) -> NullSimRunner:
    """Yield a NullSimRunner wired to the fake Redis instance."""
    return NullSimRunner(fake_redis)


# ---------------------------------------------------------------------------
# App override helpers
# ---------------------------------------------------------------------------


def _make_app(fake_redis_client, sim_runner):
    """Return the FastAPI app with test overrides applied."""
    from wargame_backend.app.main import create_app
    from wargame_backend.app.deps import get_db, get_redis, get_sim_runner

    app = create_app()

    # Override lifespan dependencies via app.state (set before routes run)
    app.state.redis = fake_redis_client
    app.state.sim_runner = sim_runner

    return app


# ---------------------------------------------------------------------------
# Async HTTP client
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def client(
    db,
    fake_redis,
    null_sim_runner,
) -> AsyncGenerator[AsyncClient, None]:
    """Yield an httpx AsyncClient that exercises the FastAPI app in-process.

    The DB session, Redis client, and SimRunner are all overridden with
    in-memory / fake implementations.
    """
    from wargame_backend.app.main import app
    from wargame_backend.app.deps import get_db, get_redis, get_sim_runner

    # Override FastAPI dependencies
    async def _override_db() -> AsyncGenerator[AsyncSession, None]:
        yield db

    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[get_redis] = lambda: fake_redis
    app.dependency_overrides[get_sim_runner] = lambda: null_sim_runner

    # Set app state (accessed directly by WS handler and readyz)
    app.state.redis = fake_redis
    app.state.sim_runner = null_sim_runner

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as ac:
        yield ac

    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Convenience factories
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def scenario(db: AsyncSession) -> Scenario:
    """Insert and return a ready scenario row."""
    sc = Scenario(
        id=uuid.uuid4(),
        title="Test Scenario",
        description="A test scenario for unit tests.",
        country_ids=["CHN", "USA"],
        initial_conditions={"posture_overrides": {}, "seed_events": []},
    )
    db.add(sc)
    await db.commit()
    await db.refresh(sc)
    return sc
