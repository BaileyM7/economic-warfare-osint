"""Trade.gov CSL signal extractor.

Surfaces a signal when new export-control / screening list entries land
naming a slice country in the recent window.  US export controls are a
high-signal economic-coercion lever — every new BIS Entity List addition
is a deliberate policy move.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Event

from wargame_ai.sim.signals import Signal
from wargame_ai.sim.extractors._base import normalize_magnitude


class TradeGovExtractor:
    source = "Trade.gov"
    _SOURCE_KEY = "trade_gov"

    async def extract(
        self,
        session: AsyncSession,
        iso3: str,
        *,
        window_hours: int = 24,
    ) -> Signal | None:
        since = datetime.now(timezone.utc) - timedelta(hours=window_hours)
        stmt = select(func.count(Event.id)).where(
            and_(
                Event.source == self._SOURCE_KEY,
                Event.ingested_at >= since,
                or_(Event.actor_iso3 == iso3, Event.target_iso3 == iso3),
            )
        )
        count = (await session.execute(stmt)).scalar_one() or 0
        if count == 0:
            return None
        magnitude = round(normalize_magnitude(count, soft_cap=3), 2)
        if magnitude < 0.1:
            return None
        headline = (
            f"{iso3}: {count} new US export-control / CSL listings (last {window_hours}h)"
        )
        return Signal(
            source=self.source,
            headline=headline[:120],
            magnitude=magnitude,
            direction="negative",
        )
