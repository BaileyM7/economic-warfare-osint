"""EIA (US Energy Information Administration) adapter.

Data source
-----------
The EIA Open Data API exposes thousands of US energy time series.  We pull
a small focused watchlist (WTI crude, Henry Hub gas, US net generation),
emit one Event per (series, daily observation), and attach a
``delta`` (current minus prior observation) for the EIA signal extractor.

API
---
``https://api.eia.gov/v2/seriesid/{series_id}/data?api_key=...``
Free key required; the adapter disables itself when ``EIA_API_KEY`` is
unset.

Dedup key
---------
``eia:{series_id}:{period}``
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

_API_BASE = "https://api.eia.gov/v2/seriesid"

# Series we fetch.  Daily-cadence series only — adding monthly aggregates
# without resampling makes the prompt block noisy.
_WATCHED_SERIES: dict[str, str] = {
    "PET.RWTC.D": "WTI crude spot",
    "NG.RNGWHHD.D": "Henry Hub gas spot",
    "PET.WTOTNUS.W": "US weekly net oil import (kbbl)",
}


class EIARawRecord(BaseModel):
    """Typed representation of one EIA observation + computed delta."""

    series_id: str
    series_label: str
    period: str  # source-defined; usually YYYY-MM-DD
    value: float
    prior_value: float | None = None
    delta: float | None = None
    raw: dict[str, Any] = Field(default_factory=dict)


def _safe_float(text: Any) -> float | None:
    if text is None:
        return None
    try:
        return float(text)
    except (TypeError, ValueError):
        return None


class EIASource(Source):
    """EIA energy data adapter — emits one Event per (series, observation)."""

    name: ClassVar[str] = "eia"
    display_name: ClassVar[str] = "EIA Energy Data"

    @property
    def enabled(self) -> bool:
        if not super().enabled:
            return False
        if not os.environ.get("EIA_API_KEY"):
            log.info("eia.disabled_no_api_key")
            return False
        return True

    async def fetch(
        self, since: datetime, until: datetime
    ) -> AsyncIterator[RawRecord]:
        api_key = os.environ.get("EIA_API_KEY", "")
        for series_id, label in _WATCHED_SERIES.items():
            url = f"{_API_BASE}/{series_id}/data"
            params = {
                "api_key": api_key,
                "start": since.strftime("%Y-%m-%d"),
                "end": until.strftime("%Y-%m-%d"),
                "sort[0][column]": "period",
                "sort[0][direction]": "asc",
            }
            try:
                response = await self._get(url, params=params)
                payload = response.json()
            except Exception as exc:  # noqa: BLE001
                log.warning(
                    "eia.fetch_failed", series_id=series_id, error=str(exc)
                )
                continue

            observations = (payload.get("response") or {}).get("data") or []
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
                yield EIARawRecord(
                    series_id=series_id,
                    series_label=label,
                    period=str(obs.get("period") or ""),
                    value=value,
                    prior_value=prior_value,
                    delta=delta,
                    raw={"period": obs.get("period"), "value": obs.get("value")},
                )
                prior_value = value

    async def normalize(self, raw: RawRecord) -> Event:
        assert isinstance(raw, EIARawRecord)
        try:
            occurred_at = datetime.strptime(raw.period[:10], "%Y-%m-%d").replace(
                tzinfo=timezone.utc
            )
        except (ValueError, TypeError):
            occurred_at = datetime.now(timezone.utc)

        dedup_key = f"eia:{raw.series_id}:{raw.period}"
        return Event(
            source="eia",
            occurred_at=occurred_at,
            actor_iso3="USA",  # EIA reports US-centric series
            target_iso3=None,
            event_type=f"energy_observation_{raw.series_id.lower().replace('.', '_')}",
            domain=EventDomain.economic,
            severity=None,
            payload={
                "_dedup_key": dedup_key,
                "series_id": raw.series_id,
                "series_label": raw.series_label,
                "period": raw.period,
                "value": raw.value,
                "prior_value": raw.prior_value,
                "delta": raw.delta,
            },
            raw_text=(
                f"EIA {raw.series_id} {raw.period}: {raw.value:+.4f}"
                + (f" (Δ {raw.delta:+.4f})" if raw.delta is not None else "")
            ),
        )
