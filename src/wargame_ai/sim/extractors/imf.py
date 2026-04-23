"""IMF macro signal extractor.

Surfaces the most recent published IMF observation in the window for the
country.  Reserve drawdowns / current-account swings are core escalation
signals — CHN reserves trending down or USD-CNY exchange-rate moves matter
for the agent's reasoning.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Event

from wargame_ai.sim.signals import Signal


class IMFExtractor:
    source = "IMF"
    _SOURCE_KEY = "imf"

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
            .limit(20)
        )
        rows = (await session.execute(stmt)).scalars().all()
        if not rows:
            return None
        # Pick the freshest observation.
        row = rows[0]
        label = (row.payload or {}).get("indicator_label", "indicator")
        value = (row.payload or {}).get("value")
        period = (row.payload or {}).get("period")
        if value is None:
            return None
        headline = f"{iso3} IMF {label} {period}: {value:+.2f} (newly published)"
        return Signal(
            source=self.source,
            headline=headline[:120],
            magnitude=0.45,
            direction="neutral",
            evidence_id=str(row.id),
        )
