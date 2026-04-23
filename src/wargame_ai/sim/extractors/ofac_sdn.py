"""OFAC SDN signal extractor (Phase C — implemented).

US Treasury OFAC publishes the Specially Designated Nationals list.  Per
turn signal: count of SDN additions this window where the entity's
nationality OR sanctioning program targets the country.  Specific entity
designations (e.g. TSMC subsidiary, SMIC) are high-signal posture moves.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from wargame_backend.app.db.models import Event

from wargame_ai.sim.signals import Signal
from wargame_ai.sim.extractors._base import normalize_magnitude


class OFACSDNExtractor:
    source = "OFAC-SDN"
    _SOURCE_KEY = "ofac_sdn"

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
        magnitude = round(normalize_magnitude(count, soft_cap=5), 2)
        if magnitude < 0.1:
            return None
        headline = f"{iso3}: {count} new OFAC SDN additions (last {window_hours}h)"
        return Signal(
            source=self.source,
            headline=headline[:120],
            magnitude=magnitude,
            direction="negative",
        )
