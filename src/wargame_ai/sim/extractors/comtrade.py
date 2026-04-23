"""UN Comtrade signal extractor (Phase B — implemented).

Comtrade publishes monthly bilateral trade flows.  Per-turn signal: large
month-over-month change in trade flow involving the country, focused on
flows where the *partner* is also one of the sim's tracked countries.  A
sudden -18% drop in CHN→TWN semiconductor exports is the kind of move
that should land in the agent's prompt.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from wargame_backend.app.db.models import Event

from wargame_ai.sim.signals import Signal


class ComtradeExtractor:
    source = "Comtrade"
    _SOURCE_KEY = "un_comtrade"

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
            .limit(50)
        )
        rows = (await session.execute(stmt)).scalars().all()
        if not rows:
            return None

        # Find the largest |mom_pct_change| in the window.  Comtrade adapter
        # is expected to populate payload.{partner_iso3, commodity, mom_pct_change}.
        best: tuple[float, Event] | None = None
        for row in rows:
            pct = (row.payload or {}).get("mom_pct_change")
            if not isinstance(pct, (int, float)):
                continue
            if best is None or abs(pct) > abs(best[0]):
                best = (float(pct), row)
        if best is None:
            return None
        pct, row = best
        if abs(pct) < 5.0:  # < 5% MoM is noise
            return None

        partner = (row.payload or {}).get("partner_iso3", "?")
        commodity = (row.payload or {}).get("commodity", "trade")
        direction = "negative" if pct < 0 else "positive"
        magnitude = round(min(1.0, abs(pct) / 50.0), 2)
        headline = (
            f"{iso3}↔{partner} {commodity} {pct:+.1f}% MoM"
        )
        return Signal(
            source=self.source,
            headline=headline[:120],
            magnitude=magnitude,
            direction=direction,
            evidence_id=str(row.id),
        )
