"""Yahoo Finance market signal extractor.

Surfaces the largest |pct_change| of the day across the country's watched
tickers (equities + bilateral USD pair).  Markets price geopolitical risk
ahead of headlines — a -4% TSMC ADR move on a quiet news day is a signal
even with no other source confirming it.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from wargame_backend.app.db.models import Event

from wargame_ai.sim.signals import Signal


class YFinanceExtractor:
    source = "Markets"
    _SOURCE_KEY = "yfinance"

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
                    Event.actor_iso3 == iso3,
                )
            )
            .order_by(Event.occurred_at.desc())
            .limit(50)
        )
        rows = (await session.execute(stmt)).scalars().all()
        if not rows:
            return None

        # Largest |pct_change| wins.
        best: tuple[float, Event] | None = None
        for row in rows:
            pct = (row.payload or {}).get("pct_change")
            if not isinstance(pct, (int, float)):
                continue
            if best is None or abs(pct) > abs(best[0]):
                best = (float(pct), row)
        if best is None:
            return None
        pct, row = best
        # Daily moves > ~1.5% are non-trivial; sub-1% is noise.
        if abs(pct) < 1.5:
            return None
        label = (row.payload or {}).get("label") or row.payload.get("ticker", "?")
        magnitude = round(min(1.0, abs(pct) / 8.0), 2)
        direction = "negative" if pct < 0 else "positive"
        headline = f"{label} closed {pct:+.2f}% (notable single-day move)"
        return Signal(
            source=self.source,
            headline=headline[:120],
            magnitude=magnitude,
            direction=direction,
            evidence_id=str(row.id),
        )
