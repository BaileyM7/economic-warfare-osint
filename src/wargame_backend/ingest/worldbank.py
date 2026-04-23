"""World Bank Indicators API adapter.

Data source
-----------
World Bank Indicators REST API v2:
  https://api.worldbank.org/v2/country/{iso3}/indicator/{indicator_id}?format=json&per_page=5&mrv=5

Indicators pulled for each of the 10 slice countries:
  NY.GDP.MKTP.CD     — GDP (current USD)
  NE.RSB.GNFS.CD     — Trade balance (goods and services, current USD)
  MS.MIL.XPND.GD.ZS  — Military expenditure (% of GDP)
  SP.POP.TOTL        — Population, total

Storage decision
----------------
These are NOT event-type records (they have no actor/target).  They are stored
as Events with:
  domain   = "economic"
  event_type = "economic_indicator"
  actor_iso3 = the country ISO3
  target_iso3 = None
  occurred_at = Jan 1 of the indicator year
  payload  = { "indicator": <code>, "value": <float|None>, "year": <int>, "_dedup_key": ... }

This allows the agent RAG pipeline to retrieve recent economic context for a
country using the same events table and the (actor_iso3, domain) index.

Severity
--------
Not meaningful for indicators; set to None.

Dedup key
---------
``worldbank:{iso3}:{indicator_id}:{year}``
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import datetime, timezone
from typing import Any, ClassVar

import structlog
from pydantic import BaseModel

from wargame_backend.app.db.models import Event, EventDomain
from ingest.base import Source, RawRecord

log = structlog.get_logger(__name__)

WB_BASE_URL = "https://api.worldbank.org/v2"

SLICE_ISO3: list[str] = [
    "CHN", "TWN", "USA", "JPN", "KOR",
    "PHL", "AUS", "PRK", "RUS", "IND",
]

INDICATORS: list[tuple[str, str]] = [
    ("NY.GDP.MKTP.CD",    "GDP (current USD)"),
    ("NE.RSB.GNFS.CD",    "Trade balance (goods and services, current USD)"),
    ("MS.MIL.XPND.GD.ZS", "Military expenditure (% of GDP)"),
    ("SP.POP.TOTL",        "Population, total"),
]

# How many most-recent values to fetch per indicator per country
MRV = 5


# ---------------------------------------------------------------------------
# Raw record model
# ---------------------------------------------------------------------------

class WorldBankRawRecord(BaseModel):
    """One indicator observation from the World Bank API."""

    iso3: str
    indicator_id: str
    indicator_label: str
    year: int
    value: float | None


# ---------------------------------------------------------------------------
# Adapter
# ---------------------------------------------------------------------------

class WorldBankSource(Source):
    """World Bank Indicators adapter.

    Pulls GDP, trade balance, military expenditure, and population for the
    10 vertical-slice countries and stores them as economic indicator Events.

    Note: The ``since``/``until`` window is used only to decide whether to
    refresh (i.e., if until is in the future relative to the latest cached
    year we always re-fetch).  The World Bank API returns the most-recent N
    values regardless of date window; we ingest all returned rows and rely
    on the dedup key to avoid duplicates.
    """

    name: ClassVar[str] = "worldbank"
    display_name: ClassVar[str] = "World Bank Indicators"

    async def fetch(
        self, since: datetime, until: datetime
    ) -> AsyncIterator[RawRecord]:
        """Yield WorldBankRawRecord for each country × indicator combination."""
        for iso3 in SLICE_ISO3:
            for indicator_id, indicator_label in INDICATORS:
                url = f"{WB_BASE_URL}/country/{iso3}/indicator/{indicator_id}"
                params: dict[str, Any] = {
                    "format": "json",
                    "per_page": MRV,
                    "mrv": MRV,
                }
                try:
                    response = await self._get(url, params=params)
                    payload = response.json()
                    # WB returns [metadata_dict, [records]] or [metadata, null]
                    if not isinstance(payload, list) or len(payload) < 2:
                        log.warning(
                            "worldbank.unexpected_shape",
                            iso3=iso3,
                            indicator=indicator_id,
                        )
                        continue
                    records = payload[1]
                    if not records:
                        log.debug(
                            "worldbank.no_data",
                            iso3=iso3,
                            indicator=indicator_id,
                        )
                        continue
                    for record in records:
                        try:
                            year_str = record.get("date", "")
                            year = int(year_str) if year_str else 0
                            raw_value = record.get("value")
                            value = float(raw_value) if raw_value is not None else None
                            yield WorldBankRawRecord(
                                iso3=iso3,
                                indicator_id=indicator_id,
                                indicator_label=indicator_label,
                                year=year,
                                value=value,
                            )
                        except Exception as exc:  # noqa: BLE001
                            log.warning(
                                "worldbank.record_parse_error",
                                iso3=iso3,
                                indicator=indicator_id,
                                error=str(exc),
                            )
                except Exception as exc:  # noqa: BLE001
                    log.warning(
                        "worldbank.fetch_error",
                        iso3=iso3,
                        indicator=indicator_id,
                        error=str(exc),
                    )

    async def normalize(self, raw: RawRecord) -> Event:
        """Map a WorldBankRawRecord to the canonical Event ORM model."""
        assert isinstance(raw, WorldBankRawRecord)

        occurred_at = datetime(raw.year, 1, 1, tzinfo=timezone.utc) if raw.year else datetime.now(timezone.utc)

        dedup_key = f"worldbank:{raw.iso3}:{raw.indicator_id}:{raw.year}"
        payload: dict[str, Any] = {
            "_dedup_key": dedup_key,
            "indicator": raw.indicator_id,
            "indicator_label": raw.indicator_label,
            "value": raw.value,
            "year": raw.year,
            "iso3": raw.iso3,
        }

        raw_text = (
            f"{raw.iso3} {raw.indicator_label}: "
            f"{raw.value:,.2f} ({raw.year})"
            if raw.value is not None
            else f"{raw.iso3} {raw.indicator_label}: N/A ({raw.year})"
        )

        return Event(
            source="worldbank",
            occurred_at=occurred_at,
            actor_iso3=raw.iso3,
            target_iso3=None,
            event_type="economic_indicator",
            domain=EventDomain.economic,  # type: ignore[arg-type]
            severity=None,
            payload=payload,
            raw_text=raw_text,
        )
