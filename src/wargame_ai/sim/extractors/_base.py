"""Shared helpers for signal extractors.

Common pattern: query the ``events`` table for rows where
``source = self.source_key`` and the country (actor or target) is ``iso3``,
within the last ``window_hours``.  ``EventQueryHelper`` factors that out so
each extractor's body can focus on the source-specific aggregation /
headline phrasing.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Sequence

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Event


async def fetch_country_events(
    session: AsyncSession,
    *,
    source_key: str,
    iso3: str,
    window_hours: int,
    limit: int = 500,
) -> Sequence[Event]:
    """Return up to ``limit`` recent ``Event`` rows for a country.

    A row matches when ``Event.source == source_key`` AND the country is
    either the actor or the target.  Ordered most-recent-first so callers
    can early-exit when computing magnitude trends.
    """
    since = datetime.now(timezone.utc) - timedelta(hours=window_hours)
    stmt = (
        select(Event)
        .where(Event.source == source_key)
        .where(Event.occurred_at >= since)
        .where(or_(Event.actor_iso3 == iso3, Event.target_iso3 == iso3))
        .order_by(Event.occurred_at.desc())
        .limit(limit)
    )
    result = await session.execute(stmt)
    return result.scalars().all()


def normalize_magnitude(value: float, *, soft_cap: float) -> float:
    """Map an unbounded count/score to the [0, 1] magnitude scale.

    Uses a soft saturating curve so very large values don't all collapse to
    1.0 — preserves rank ordering between "large" and "huge" signals.
    """
    if value <= 0:
        return 0.0
    return min(1.0, value / (value + soft_cap))
