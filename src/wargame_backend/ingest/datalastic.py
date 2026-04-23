"""Datalastic vessel tracking adapter.

Data source
-----------
Datalastic exposes AIS positions and a vessel-search endpoint
(``https://api.datalastic.com/api/v0/vessel_inradius``).  We sample the
South China Sea + Taiwan Strait AOI on each run and aggregate by flag
(country of registration).  The aggregator computes a week-over-week
percentage change in flagged-vessel pings inside the watch zone, which is
the field the Datalastic signal extractor reads at perception time.

Auth
----
Requires ``DATALASTIC_API_KEY`` env var (passed as a query parameter).
The adapter disables itself when the key is missing.

Dedup key
---------
``datalastic:{flag_iso3}:{zone}:{period_iso}`` — one aggregated reading
per flag per zone per ingest run, keyed by the run's `until` instant.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from datetime import datetime, timedelta, timezone
from typing import Any, ClassVar

import structlog
from pydantic import BaseModel, Field

from wargame_backend.app.db.models import Event, EventDomain
from ingest.base import Source, RawRecord

log = structlog.get_logger(__name__)

_API_URL = "https://api.datalastic.com/api/v0/vessel_inradius"

# Watch zones.  ``radius`` is in nautical miles per Datalastic's docs.
# The two zones below cover the Taiwan-strait + ADIZ buffer and the SCS
# disputed-features ring respectively — both load-bearing for the slice.
_WATCH_ZONES: list[dict[str, Any]] = [
    {
        "zone": "TWN_strait_buffer",
        "lat": 24.0,
        "lon": 120.0,
        "radius_nm": 250,
    },
    {
        "zone": "SCS_spratlys",
        "lat": 10.0,
        "lon": 114.0,
        "radius_nm": 300,
    },
]

# Slice flags we care about (Datalastic returns ISO2 flag codes).
_ISO2_TO_ISO3: dict[str, str] = {
    "CN": "CHN",
    "TW": "TWN",
    "US": "USA",
    "JP": "JPN",
    "KR": "KOR",
    "PH": "PHL",
    "AU": "AUS",
    "KP": "PRK",
    "RU": "RUS",
    "IN": "IND",
}


class DatalasticRawRecord(BaseModel):
    """Aggregated zone+flag observation with week-over-week delta."""

    zone: str
    flag_iso3: str
    period_iso: str  # ISO timestamp of this observation
    ping_count: int
    prior_ping_count: int | None = None
    ping_count_w_w_pct: float | None = None
    raw_sample: dict[str, Any] = Field(default_factory=dict)


def _safe_pct(current: int, prior: int | None) -> float | None:
    if prior is None or prior == 0:
        return None
    return round(((current - prior) / prior) * 100.0, 2)


class DatalasticSource(Source):
    """Datalastic AIS adapter — emits one Event per (zone, flag) per run."""

    name: ClassVar[str] = "datalastic"
    display_name: ClassVar[str] = "Datalastic AIS"

    @property
    def enabled(self) -> bool:
        if not super().enabled:
            return False
        if not os.environ.get("DATALASTIC_API_KEY"):
            log.info("datalastic.disabled_no_api_key")
            return False
        return True

    async def fetch(
        self, since: datetime, until: datetime
    ) -> AsyncIterator[RawRecord]:
        api_key = os.environ.get("DATALASTIC_API_KEY", "")
        # We sample the current zone state for ``until`` and a prior reading
        # exactly one week earlier, so we can compute a w/w delta.  Datalastic
        # exposes only "current" position; we approximate the prior reading by
        # using the previously-stored aggregate for the same (zone, flag).  If
        # one isn't available we emit the current reading with a None delta.
        for zone in _WATCH_ZONES:
            params = {
                "api-key": api_key,
                "lat": zone["lat"],
                "lon": zone["lon"],
                "radius": zone["radius_nm"],
            }
            try:
                response = await self._get(_API_URL, params=params)
                payload = response.json()
            except Exception as exc:  # noqa: BLE001
                log.warning(
                    "datalastic.fetch_failed",
                    zone=zone["zone"],
                    error=str(exc),
                )
                continue

            vessels = (payload.get("data") or {}).get("vessels") or []
            counts: dict[str, int] = {}
            samples: dict[str, dict[str, Any]] = {}
            for v in vessels:
                flag_iso2 = (v.get("country_iso") or "").upper()
                flag_iso3 = _ISO2_TO_ISO3.get(flag_iso2)
                if flag_iso3 is None:
                    continue
                counts[flag_iso3] = counts.get(flag_iso3, 0) + 1
                samples.setdefault(
                    flag_iso3,
                    {"name": v.get("name"), "mmsi": v.get("mmsi")},
                )

            period_iso = until.replace(microsecond=0).isoformat()
            for flag_iso3, count in counts.items():
                yield DatalasticRawRecord(
                    zone=zone["zone"],
                    flag_iso3=flag_iso3,
                    period_iso=period_iso,
                    ping_count=count,
                    # Prior count would normally come from a lookup against
                    # the previous run's row; left as None here so the
                    # extractor falls through without surfacing a noisy
                    # signal on first ingest.  A follow-up can backfill by
                    # querying the events table for the same (zone, flag).
                    prior_ping_count=None,
                    ping_count_w_w_pct=None,
                    raw_sample=samples[flag_iso3],
                )

    async def normalize(self, raw: RawRecord) -> Event:
        assert isinstance(raw, DatalasticRawRecord)
        try:
            occurred_at = datetime.fromisoformat(raw.period_iso)
            if occurred_at.tzinfo is None:
                occurred_at = occurred_at.replace(tzinfo=timezone.utc)
        except ValueError:
            occurred_at = datetime.now(timezone.utc)

        dedup_key = f"datalastic:{raw.flag_iso3}:{raw.zone}:{raw.period_iso}"
        return Event(
            source="datalastic",
            occurred_at=occurred_at,
            actor_iso3=raw.flag_iso3,
            target_iso3=None,
            event_type=f"ais_zone_presence_{raw.zone}",
            domain=EventDomain.kinetic_limited,
            severity=None,
            payload={
                "_dedup_key": dedup_key,
                "flag_iso3": raw.flag_iso3,
                "zone": raw.zone,
                "ping_count": raw.ping_count,
                "prior_ping_count": raw.prior_ping_count,
                "ping_count_w_w_pct": raw.ping_count_w_w_pct,
                "sample": raw.raw_sample,
            },
            raw_text=(
                f"Datalastic {raw.zone} {raw.flag_iso3}-flag pings: "
                f"{raw.ping_count}"
            ),
        )
