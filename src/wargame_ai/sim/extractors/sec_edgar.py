"""SEC EDGAR signal extractor.

Surfaces a signal when there's a spike in filings (8-K, 10-K, 20-F, 10-Q)
mentioning the target country in a risk-disclosure context.  Corporate
disclosures often lead government action — TSMC's risk-factor language
about cross-strait tensions changes ahead of policy moves.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Event

from wargame_ai.sim.signals import Signal
from wargame_ai.sim.extractors._base import normalize_magnitude


class SECEdgarExtractor:
    source = "EDGAR"
    _SOURCE_KEY = "sec_edgar"

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
        magnitude = round(normalize_magnitude(count, soft_cap=8), 2)
        if magnitude < 0.1:
            return None
        headline = (
            f"{iso3}-related SEC filings: {count} new (8-K/10-K/20-F) in "
            f"last {window_hours}h"
        )
        return Signal(
            source=self.source,
            headline=headline[:120],
            magnitude=magnitude,
            direction="neutral",
        )
