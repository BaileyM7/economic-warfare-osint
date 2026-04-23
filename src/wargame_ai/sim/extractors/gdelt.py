"""GDELT signal extractor.

GDELT 2.0 publishes a 15-min cadence event stream coded against CAMEO.  The
ingest layer normalizes each row into the canonical ``events`` table with
the CAMEO ``event_type`` preserved and ``severity`` carrying the Goldstein
score (-10 = most hostile, +10 = most cooperative).

Per-turn signal: average Goldstein tone of the country's involvement over
the last ``window_hours``, contrasted with the prior window of the same
length.  We only emit when the delta is large enough to clear the
collector's magnitude floor — small drift is silence.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from wargame_backend.app.db.models import Event

from wargame_ai.sim.signals import Signal


class GDELTExtractor:
    source = "GDELT"
    _SOURCE_KEY = "gdelt"

    async def extract(
        self,
        session: AsyncSession,
        iso3: str,
        *,
        window_hours: int = 24,
    ) -> Signal | None:
        now = datetime.now(timezone.utc)
        recent_start = now - timedelta(hours=window_hours)
        prior_start = now - timedelta(hours=window_hours * 2)

        async def _avg_tone(start: datetime, end: datetime) -> tuple[float | None, int]:
            stmt = select(
                func.avg(Event.severity), func.count(Event.id)
            ).where(
                and_(
                    Event.source == self._SOURCE_KEY,
                    Event.occurred_at >= start,
                    Event.occurred_at < end,
                    or_(Event.actor_iso3 == iso3, Event.target_iso3 == iso3),
                )
            )
            row = (await session.execute(stmt)).one()
            avg = float(row[0]) if row[0] is not None else None
            count = int(row[1] or 0)
            return avg, count

        recent_avg, recent_count = await _avg_tone(recent_start, now)
        if recent_count == 0:
            return None  # no GDELT activity for this country in window

        prior_avg, _ = await _avg_tone(prior_start, recent_start)

        # Magnitude scales with both event volume and tone-shift size.  When
        # there's no prior baseline, fall back to absolute-tone magnitude so
        # very hostile spikes still surface on a cold start.
        if prior_avg is None:
            shift = 0.0
            tone_mag = min(1.0, abs(recent_avg) / 10.0)
        else:
            shift = recent_avg - prior_avg
            tone_mag = min(1.0, abs(shift) / 6.0)
        volume_mag = min(1.0, recent_count / (recent_count + 50))
        magnitude = round(0.6 * tone_mag + 0.4 * volume_mag, 2)
        if magnitude < 0.1:
            return None

        direction = "negative" if recent_avg < (prior_avg or 0) else (
            "positive" if recent_avg > (prior_avg or 0) else "neutral"
        )
        if prior_avg is None:
            shift_phrase = f"avg tone {recent_avg:+.1f} ({recent_count} events)"
        else:
            shift_phrase = (
                f"avg tone {recent_avg:+.1f} (was {prior_avg:+.1f}, "
                f"{recent_count} events)"
            )
        headline = f"{iso3} GDELT involvement: {shift_phrase}"
        return Signal(
            source=self.source,
            headline=headline[:120],
            magnitude=magnitude,
            direction=direction,
        )
