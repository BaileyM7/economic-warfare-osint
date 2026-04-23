"""Datalastic AIS signal extractor (Phase B — implemented).

Datalastic provides AIS ship-tracking data.  Per-turn signal: count of
flagged-vessel pings inside a watch zone for the country (e.g. PLA-flagged
vessels in TWN's ADIZ buffer).  The ingest adapter is expected to populate
``payload.{flag_iso3, zone, ping_count_w_w_pct}`` so we can emit a
"week-over-week change in flagged pings" headline.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Event

from wargame_ai.sim.signals import Signal


class DatalasticExtractor:
    source = "Datalastic"
    _SOURCE_KEY = "datalastic"

    async def extract(
        self,
        session: AsyncSession,
        iso3: str,
        *,
        window_hours: int = 24,
    ) -> Signal | None:
        since = datetime.now(timezone.utc) - timedelta(hours=window_hours)
        stmt = (
            select(Event)
            .where(
                and_(
                    Event.source == self._SOURCE_KEY,
                    Event.ingested_at >= since,
                    or_(Event.actor_iso3 == iso3, Event.target_iso3 == iso3),
                )
            )
            .order_by(Event.occurred_at.desc())
            .limit(20)
        )
        rows = (await session.execute(stmt)).scalars().all()
        if not rows:
            return None

        # Pick the largest |w/w pct change| ping observation in the window.
        best: tuple[float, Event] | None = None
        for row in rows:
            pct = (row.payload or {}).get("ping_count_w_w_pct")
            if not isinstance(pct, (int, float)):
                continue
            if best is None or abs(pct) > abs(best[0]):
                best = (float(pct), row)
        if best is None:
            return None
        pct, row = best
        if abs(pct) < 25.0:
            return None

        flag = (row.payload or {}).get("flag_iso3", "?")
        zone = (row.payload or {}).get("zone", "watch zone")
        magnitude = round(min(1.0, abs(pct) / 200.0), 2)
        direction = "negative" if pct > 0 else "positive"
        headline = f"{flag}-flagged AIS pings in {zone}: {pct:+.0f}% w/w"
        return Signal(
            source=self.source,
            headline=headline[:120],
            magnitude=magnitude,
            direction=direction,
            evidence_id=str(row.id),
        )
