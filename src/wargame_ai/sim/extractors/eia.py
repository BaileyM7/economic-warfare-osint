"""EIA energy signal extractor (Phase C — implemented).

EIA publishes US energy series — crude prices, LNG exports, electricity
demand.  Per-turn signal: large move in a watched series since the prior
published observation.  Energy is a strategic constraint for the
Taiwan-2027 slice (CHN imports, TWN imports, USA exports), so we surface
the signal on every country's perception.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from wargame_backend.app.db.models import Event

from wargame_ai.sim.signals import Signal


class EIAExtractor:
    source = "EIA"
    _SOURCE_KEY = "eia"
    _WATCHED = {
        "PET.RWTC.D": ("WTI crude", 3.0),
        "NG.RNGWHHD.D": ("Henry Hub gas", 0.3),
        "ELEC.GEN.ALL-US-99.M": ("US net generation", 5.0),
    }

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
                )
            )
            .order_by(Event.occurred_at.desc())
            .limit(50)
        )
        rows = (await session.execute(stmt)).scalars().all()
        if not rows:
            return None

        best: tuple[float, Event, str] | None = None
        for row in rows:
            series = (row.payload or {}).get("series_id")
            if series not in self._WATCHED:
                continue
            label, threshold = self._WATCHED[series]
            delta = (row.payload or {}).get("delta")
            value = (row.payload or {}).get("value")
            if not isinstance(delta, (int, float)) or not isinstance(value, (int, float)):
                continue
            if abs(delta) < threshold:
                continue
            if best is None or abs(delta) > best[0]:
                best = (abs(delta), row, f"{label} {value:.2f} ({delta:+.2f})")
        if best is None:
            return None
        _, row, headline = best
        magnitude = round(min(1.0, best[0] / (best[0] + 5.0)), 2)
        return Signal(
            source=self.source,
            headline=headline[:120],
            magnitude=magnitude,
            direction="neutral",
            evidence_id=str(row.id),
        )
