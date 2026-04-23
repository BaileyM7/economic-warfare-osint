"""IMF SDMX data adapter.

Data source
-----------
The IMF exposes SDMX-JSON endpoints at
``https://api.imf.org/external/sdmx/2.1/data/{dataset}/{key}``.
We pull a small set of country-quarterly indicators (current account
balance, total reserves, real GDP growth) for each slice country and emit
one Event per (country, indicator, period).

Auth
----
None — public.

Dedup key
---------
``imf:{dataset}:{indicator}:{iso3}:{period}``
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import datetime, timezone
from typing import Any, ClassVar

import structlog
from pydantic import BaseModel, Field

from wargame_backend.app.db.models import Event, EventDomain
from ingest.base import Source, RawRecord

log = structlog.get_logger(__name__)

# IMF SDMX uses ISO2 country codes for most datasets.
_ISO3_TO_IMF: dict[str, str] = {
    "CHN": "CN", "TWN": "TW", "USA": "US", "JPN": "JP", "KOR": "KR",
    "PHL": "PH", "AUS": "AU", "PRK": "KP", "RUS": "RU", "IND": "IN",
}

# (dataset, indicator) → human label.  IFS = International Financial Statistics;
# codes verified against IMF's SDMX CompactData catalog.
#
# TODO(user): this watchlist is a domain-knowledge call.  The three below are
# the "load-bearing" macro readings for the Taiwan-2027 slice, but feel free
# to extend/replace.  See the full IFS code list at:
# http://dataservices.imf.org/REST/SDMX_JSON.svc/DataStructure/IFS
_WATCHED: list[tuple[str, str, str]] = [
    # End-of-period total reserves excluding gold (USD) — CHN draw-downs here
    # are one of the earliest signs of currency defense under pressure.
    ("IFS", "RAXG_USD", "Reserves excl. gold (USD, end of period)"),
    # End-of-period exchange rate, local currency per USD — tracks RMB/JPY/TWD
    # devaluation pressure relative to the dollar.
    ("IFS", "ENDE_XDC_USD_RATE", "Exchange rate (local per USD, end-period)"),
    # CPI index (all items) — real inflation readings.
    ("IFS", "PCPI_IX", "CPI index (all items)"),
]

# Legacy CompactData endpoint is the only one IMF still publishes free-tier
# JSON from reliably — the newer /external/sdmx/2.1/data path 404s on most
# dimension keys as of early 2026.
_API_BASE = "https://dataservices.imf.org/REST/SDMX_JSON.svc/CompactData"


class IMFRawRecord(BaseModel):
    """One IMF observation tagged with country + indicator label."""

    dataset: str
    indicator: str
    indicator_label: str
    iso3: str
    period: str  # YYYY-Qn or YYYY
    value: float
    raw: dict[str, Any] = Field(default_factory=dict)


class IMFSource(Source):
    """IMF SDMX adapter — emits one Event per (country, indicator, period)."""

    name: ClassVar[str] = "imf"
    display_name: ClassVar[str] = "IMF SDMX"

    async def fetch(
        self, since: datetime, until: datetime
    ) -> AsyncIterator[RawRecord]:
        # CompactData URL shape: /{dataset}/{FREQ}.{REF_AREA}.{INDICATOR}
        # Frequency Q = quarterly; IFS also supports M (monthly) and A (annual).
        start_period = since.strftime("%Y")
        end_period = until.strftime("%Y")
        for dataset, indicator, label in _WATCHED:
            for iso3, imf_code in _ISO3_TO_IMF.items():
                key = f"Q.{imf_code}.{indicator}"
                url = f"{_API_BASE}/{dataset}/{key}"
                params = {"startPeriod": start_period, "endPeriod": end_period}
                try:
                    response = await self._get(url, params=params)
                    payload = response.json()
                except Exception as exc:  # noqa: BLE001
                    log.warning(
                        "imf.fetch_failed",
                        dataset=dataset,
                        indicator=indicator,
                        iso3=iso3,
                        error=str(exc),
                    )
                    continue

                # CompactData → CompactData.DataSet.Series.Obs
                # Series may be a list OR a single dict when only one series
                # matches the key; handle both.
                try:
                    dataset_obj = (payload.get("CompactData") or {}).get("DataSet") or {}
                    series_raw = dataset_obj.get("Series")
                    if series_raw is None:
                        continue
                    series_list = (
                        series_raw if isinstance(series_raw, list) else [series_raw]
                    )
                    for ser in series_list:
                        obs_raw = ser.get("Obs")
                        if obs_raw is None:
                            continue
                        obs_list = (
                            obs_raw if isinstance(obs_raw, list) else [obs_raw]
                        )
                        for obs in obs_list:
                            try:
                                value = float(obs.get("@OBS_VALUE"))
                            except (TypeError, ValueError):
                                continue
                            period = str(obs.get("@TIME_PERIOD") or "")
                            yield IMFRawRecord(
                                dataset=dataset,
                                indicator=indicator,
                                indicator_label=label,
                                iso3=iso3,
                                period=period,
                                value=value,
                                raw={"obs": obs},
                            )
                except Exception as exc:  # noqa: BLE001
                    log.warning(
                        "imf.parse_failed",
                        dataset=dataset,
                        indicator=indicator,
                        iso3=iso3,
                        error=str(exc),
                    )

    async def normalize(self, raw: RawRecord) -> Event:
        assert isinstance(raw, IMFRawRecord)
        # Period parses as either YYYY or YYYY-Qn.  We anchor to the first
        # day of the period for Event ordering.
        occurred_at = _period_to_dt(raw.period) or datetime.now(timezone.utc)
        dedup_key = f"imf:{raw.dataset}:{raw.indicator}:{raw.iso3}:{raw.period}"
        return Event(
            source="imf",
            occurred_at=occurred_at,
            actor_iso3=raw.iso3,
            target_iso3=None,
            event_type=f"imf_observation_{raw.indicator.lower()}",
            domain=EventDomain.economic,
            severity=None,
            payload={
                "_dedup_key": dedup_key,
                "dataset": raw.dataset,
                "indicator": raw.indicator,
                "indicator_label": raw.indicator_label,
                "iso3": raw.iso3,
                "period": raw.period,
                "value": raw.value,
            },
            raw_text=f"IMF {raw.indicator_label} {raw.iso3} {raw.period}: {raw.value:+.2f}",
        )


def _period_to_dt(period: str) -> datetime | None:
    """Parse YYYY or YYYY-Qn into a UTC datetime at first-of-period."""
    if not period:
        return None
    try:
        if "Q" in period:
            year_str, q_str = period.split("-Q") if "-Q" in period else (period[:4], period[-1])
            year = int(year_str)
            quarter = int(q_str)
            month = (quarter - 1) * 3 + 1
            return datetime(year, month, 1, tzinfo=timezone.utc)
        return datetime(int(period), 1, 1, tzinfo=timezone.utc)
    except (ValueError, IndexError):
        return None
