"""FRED signal extractor (Phase C — implemented).

FRED publishes US macro time series (DGS10, DTWEXBGS, etc.).  Per-turn
signal: a watched series moved by more than its noise threshold since the
prior published value.  Country gating: most series only matter for USA;
we surface them on every country's perception with the assumption that a
50bps move in US 10Y yields is material to all sim participants.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from wargame_backend.app.db.models import Event

from wargame_ai.sim.signals import Signal


class FREDExtractor:
    source = "FRED"
    _SOURCE_KEY = "fred"
    # Series we treat as material for all sim participants (key, label, threshold).
    _WATCHED = {
        "DGS10": ("US 10Y yield", 0.25),
        "DTWEXBGS": ("USD broad index", 1.0),
        "DCOILWTICO": ("WTI crude", 5.0),
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

        best: tuple[float, str, str, Event] | None = None  # (abs_delta, label, fmt, row)
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
                best = (
                    abs(delta),
                    label,
                    f"{label} {value:+.2f} ({delta:+.2f})",
                    row,
                )
        if best is None:
            return None
        _, label, fmt, row = best
        magnitude = round(min(1.0, best[0] / (best[0] + 1.0)), 2)
        return Signal(
            source=self.source,
            headline=fmt[:120],
            magnitude=magnitude,
            direction="neutral",
            evidence_id=str(row.id),
        )
