"""ACLED signal extractor.

ACLED (Armed Conflict Location & Event Data) tracks discrete violent events
with fatalities counts.  Per-turn signal: count of recent events involving
the country plus total fatalities, normalized so a small spike in a quiet
country reads as material while a steady war hum stays at modest magnitude.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from wargame_backend.app.db.models import Event

from wargame_ai.sim.signals import Signal
from wargame_ai.sim.extractors._base import normalize_magnitude


class ACLEDExtractor:
    source = "ACLED"
    _SOURCE_KEY = "acled"

    async def extract(
        self,
        session: AsyncSession,
        iso3: str,
        *,
        window_hours: int = 24,
    ) -> Signal | None:
        since = datetime.now(timezone.utc) - timedelta(hours=window_hours)
        # Sum fatalities (stored in payload->>'fatalities') and count events.
        fatalities_expr = func.coalesce(
            func.sum(
                func.cast(Event.payload["fatalities"].astext, type_=func.Integer().type)
            ),
            0,
        )
        stmt = select(func.count(Event.id), fatalities_expr).where(
            and_(
                Event.source == self._SOURCE_KEY,
                Event.occurred_at >= since,
                or_(Event.actor_iso3 == iso3, Event.target_iso3 == iso3),
            )
        )
        try:
            row = (await session.execute(stmt)).one()
        except Exception:
            # JSONB cast is Postgres-only; on SQLite fall back to a count-only
            # query so the extractor still returns sensible signal shape.
            count_only = (
                await session.execute(
                    select(func.count(Event.id)).where(
                        and_(
                            Event.source == self._SOURCE_KEY,
                            Event.occurred_at >= since,
                            or_(
                                Event.actor_iso3 == iso3,
                                Event.target_iso3 == iso3,
                            ),
                        )
                    )
                )
            ).scalar_one()
            row = (count_only, 0)

        count = int(row[0] or 0)
        fatalities = int(row[1] or 0)
        if count == 0:
            return None

        magnitude = round(
            0.5 * normalize_magnitude(count, soft_cap=20)
            + 0.5 * normalize_magnitude(fatalities, soft_cap=50),
            2,
        )
        if magnitude < 0.1:
            return None

        headline = (
            f"{iso3} ACLED: {count} violent events, {fatalities} fatalities "
            f"(last {window_hours}h)"
        )
        return Signal(
            source=self.source,
            headline=headline[:120],
            magnitude=magnitude,
            direction="negative",
        )
