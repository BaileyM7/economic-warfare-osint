"""FRED (Federal Reserve Economic Data) macro time-series adapter.

Data source
-----------
The St. Louis Fed publishes thousands of US macro/financial series.  We pull
a small watchlist of high-signal daily series and emit one Event per
observation, attaching the day-over-prior-observation ``delta`` so the
FRED signal extractor at ``ai/sim/extractors/fred.py`` can surface
material moves (e.g. DGS10 +0.30 basis-point shift).

API
---
``https://api.stlouisfed.org/fred/series/observations``
Requires ``FRED_API_KEY`` (free, 30-second signup).

Dedup key
---------
``fred:{series_id}:{date}`` — one observation per series per day.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from datetime import datetime, timezone
from typing import Any, ClassVar

import structlog
from pydantic import BaseModel, Field

from wargame_backend.app.db.models import Event, EventDomain
from ingest.base import Source, RawRecord

log = structlog.get_logger(__name__)

_API_URL = "https://api.stlouisfed.org/fred/series/observations"

# Watched series — keep this list short and high-signal.  Anything added
# here is also worth surfacing in the agent prompt; if a series wouldn't
# be cited by an agent, it doesn't need ingestion.
_WATCHED_SERIES: dict[str, str] = {
    "DGS10": "US 10Y Treasury yield",
    "DTWEXBGS": "USD broad index",
    "DCOILWTICO": "WTI crude oil price",
    "FEDFUNDS": "Federal Funds rate",
    "VIXCLS": "VIX equity volatility index",
}


class FREDRawRecord(BaseModel):
    """Typed representation of one FRED observation + computed delta."""

    series_id: str
    series_label: str
    date: str  # YYYY-MM-DD
    value: float
    prior_value: float | None = None
    delta: float | None = None
    raw: dict[str, Any] = Field(default_factory=dict)


def _safe_float(text: str | None) -> float | None:
    if text is None:
        return None
    text = text.strip()
    if not text or text in (".", "NA"):
        return None
    try:
        return float(text)
    except ValueError:
        return None


class FREDSource(Source):
    """FRED macro adapter — emits one Event per (series, observation) in window."""

    name: ClassVar[str] = "fred"
    display_name: ClassVar[str] = "FRED Macro Data"

    @property
    def enabled(self) -> bool:
        if not super().enabled:
            return False
        if not os.environ.get("FRED_API_KEY"):
            log.info("fred.disabled_no_api_key")
            return False
        return True

    async def fetch(
        self, since: datetime, until: datetime
    ) -> AsyncIterator[RawRecord]:
        api_key = os.environ.get("FRED_API_KEY", "")
        # Pull a small look-back so we always have a prior observation to
        # compute delta against, even on the first day of the window.
        observation_start = since.strftime("%Y-%m-%d")
        observation_end = until.strftime("%Y-%m-%d")

        for series_id, label in _WATCHED_SERIES.items():
            params = {
                "series_id": series_id,
                "api_key": api_key,
                "file_type": "json",
                "observation_start": observation_start,
                "observation_end": observation_end,
                "sort_order": "asc",
            }
            try:
                response = await self._get(_API_URL, params=params)
                payload = response.json()
            except Exception as exc:  # noqa: BLE001
                log.warning(
                    "fred.fetch_failed", series_id=series_id, error=str(exc)
                )
                continue

            observations = payload.get("observations") or []
            prior_value: float | None = None
            for obs in observations:
                value = _safe_float(obs.get("value"))
                if value is None:
                    continue
                delta = (
                    round(value - prior_value, 4)
                    if prior_value is not None
                    else None
                )
                yield FREDRawRecord(
                    series_id=series_id,
                    series_label=label,
                    date=str(obs.get("date") or ""),
                    value=value,
                    prior_value=prior_value,
                    delta=delta,
                    raw={"date": obs.get("date"), "value": obs.get("value")},
                )
                prior_value = value

    async def normalize(self, raw: RawRecord) -> Event:
        assert isinstance(raw, FREDRawRecord)
        try:
            occurred_at = datetime.strptime(raw.date, "%Y-%m-%d").replace(
                tzinfo=timezone.utc
            )
        except ValueError:
            occurred_at = datetime.now(timezone.utc)

        dedup_key = f"fred:{raw.series_id}:{raw.date}"
        # FRED is a US macro feed — actor is USA, no specific target.
        return Event(
            source="fred",
            occurred_at=occurred_at,
            actor_iso3="USA",
            target_iso3=None,
            event_type=f"macro_observation_{raw.series_id.lower()}",
            domain=EventDomain.economic,
            severity=None,
            payload={
                "_dedup_key": dedup_key,
                "series_id": raw.series_id,
                "series_label": raw.series_label,
                "date": raw.date,
                "value": raw.value,
                "prior_value": raw.prior_value,
                "delta": raw.delta,
            },
            raw_text=(
                f"FRED {raw.series_id} {raw.date}: {raw.value:+.4f}"
                + (f" (Δ {raw.delta:+.4f})" if raw.delta is not None else "")
            ),
        )
