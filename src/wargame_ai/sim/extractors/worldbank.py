"""World Bank signal extractor.

World Bank indicator releases are slow-moving (annual / quarterly).  On a
24-hour window the typical case is "no signal."  We surface a signal only
when a recently-ingested indicator row materially shifts an indicator we
care about (GDP growth, current account balance) for the requested
country.

This extractor intentionally returns None most turns.  That's the whole
point of the elegance design: silence is the default.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from wargame_backend.app.db.models import Event

from wargame_ai.sim.signals import Signal


class WorldBankExtractor:
    source = "WorldBank"
    _SOURCE_KEY = "worldbank"
    # Indicators whose movement we treat as material enough to surface.
    _WATCHED_INDICATORS = {
        "NY.GDP.MKTP.KD.ZG": "GDP growth",
        "BN.CAB.XOKA.GD.ZS": "current account / GDP",
        "FP.CPI.TOTL.ZG": "CPI inflation",
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
                    or_(Event.actor_iso3 == iso3, Event.target_iso3 == iso3),
                )
            )
            .order_by(Event.occurred_at.desc())
            .limit(20)
        )
        rows = (await session.execute(stmt)).scalars().all()
        if not rows:
            return None

        # Pick the first row whose payload indicator code is on our watch
        # list; ignore the rest to keep the headline focused.
        for row in rows:
            indicator = (row.payload or {}).get("indicator_code")
            value = (row.payload or {}).get("value")
            if indicator in self._WATCHED_INDICATORS and value is not None:
                label = self._WATCHED_INDICATORS[indicator]
                headline = f"{iso3} {label}: {value:.2f} (newly published)"
                # WB releases are point-in-time facts, not crises — modest
                # magnitude unless caller pairs with a delta calculation.
                return Signal(
                    source=self.source,
                    headline=headline[:120],
                    magnitude=0.4,
                    direction="neutral",
                    evidence_id=str(row.id),
                )
        return None
