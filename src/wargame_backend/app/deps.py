"""FastAPI dependency functions.

All dependencies are declared here and imported by route handlers.
Avoids circular imports by accessing ``app.state`` through the ``Request``
object rather than importing the lifespan-created singletons directly.

Available:
  - ``get_db``          → yields an ``AsyncSession``
  - ``get_redis``       → returns the app-level Redis client
  - ``get_settings``    → returns the cached ``Settings`` singleton
  - ``get_sim_runner``  → returns the ``SimRunner`` injected at startup
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import TYPE_CHECKING

import redis.asyncio as aioredis
from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from wargame_backend.app.config import Settings
from wargame_backend.app.config import get_settings as _get_settings
from wargame_backend.app.db.session import AsyncSessionLocal
from wargame_backend.app.sim_runner import SimRunner

if TYPE_CHECKING:
    pass


# ---------------------------------------------------------------------------
# Database session
# ---------------------------------------------------------------------------


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """Yield a transactional async database session.

    Commits automatically on success; rolls back on any exception.
    """
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


# ---------------------------------------------------------------------------
# Redis client
# ---------------------------------------------------------------------------


def get_redis(request: Request) -> aioredis.Redis:  # type: ignore[type-arg]
    """Return the app-level Redis client from ``app.state``."""
    return request.app.state.redis  # type: ignore[no-any-return]


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------


def get_settings() -> Settings:
    """Return the cached application settings singleton."""
    return _get_settings()


# ---------------------------------------------------------------------------
# SimRunner
# ---------------------------------------------------------------------------


def get_sim_runner(request: Request) -> SimRunner:
    """Return the ``SimRunner`` injected into ``app.state`` at startup."""
    return request.app.state.sim_runner  # type: ignore[no-any-return]


# ---------------------------------------------------------------------------
# Convenience type aliases for annotated injection
# ---------------------------------------------------------------------------

DbSession = AsyncGenerator[AsyncSession, None]
SettingsDep = Depends(get_settings)
