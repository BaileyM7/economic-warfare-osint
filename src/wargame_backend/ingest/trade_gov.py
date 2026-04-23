"""Trade.gov / ITA (International Trade Administration) adapter.

Data source
-----------
The ITA hosts the Consolidated Screening List (CSL) at
``https://api.trade.gov/v1/consolidated_screening_list/search`` — a unified
feed across BIS Entity List, OFAC SDN, State DDTC, etc.  We dedupe against
``ofac_sdn`` (already ingested separately) by attaching the source agency
to the dedup key, then emit one Event per *new* CSL entry whose
``start_date`` falls in the window.

Auth
----
Requires ``TRADE_GOV_API_KEY`` env var.

Dedup key
---------
``trade_gov:{source_id}`` where ``source_id`` is the CSL row id (combination
of source agency + entry id).
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

_CSL_URL = "https://api.trade.gov/v1/consolidated_screening_list/search"

# CSL country names → ISO3 (slice only)
_COUNTRY_TO_ISO3: dict[str, str] = {
    "China": "CHN",
    "Hong Kong": "CHN",
    "Taiwan": "TWN",
    "United States": "USA",
    "Japan": "JPN",
    "South Korea": "KOR",
    "Korea, South": "KOR",
    "North Korea": "PRK",
    "Korea, North": "PRK",
    "Philippines": "PHL",
    "Australia": "AUS",
    "Russia": "RUS",
    "India": "IND",
}


class TradeGovRawRecord(BaseModel):
    """Typed representation of one CSL entry."""

    entry_id: str
    name: str
    source_agency: str  # e.g. "Entity List (EL)" — BIS, OFAC, State, etc.
    country: str | None = None
    start_date: datetime | None = None
    addresses: list[str] = Field(default_factory=list)
    raw: dict[str, Any] = Field(default_factory=dict)


def _parse_date(text: str | None) -> datetime | None:
    if not text:
        return None
    for fmt in ("%Y-%m-%d", "%m/%d/%Y"):
        try:
            return datetime.strptime(text[:10], fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def _result_to_raw(result: dict[str, Any]) -> TradeGovRawRecord | None:
    entry_id = result.get("id") or result.get("source_id")
    if not entry_id:
        return None
    addresses = [
        (a.get("country") or "")
        for a in (result.get("addresses") or [])
        if isinstance(a, dict)
    ]
    country = next((c for c in addresses if c), None)
    return TradeGovRawRecord(
        entry_id=str(entry_id),
        name=str(result.get("name") or "(unnamed)"),
        source_agency=str(result.get("source") or "unknown"),
        country=country,
        start_date=_parse_date(result.get("start_date")),
        addresses=[a for a in addresses if a],
        raw={
            "id": entry_id,
            "name": result.get("name"),
            "source": result.get("source"),
            "type": result.get("type"),
            "programs": result.get("programs"),
            "start_date": result.get("start_date"),
        },
    )


class TradeGovSource(Source):
    """Trade.gov CSL adapter — emits one Event per new screening list entry."""

    name: ClassVar[str] = "trade_gov"
    display_name: ClassVar[str] = "Trade.gov ITA (CSL)"

    @property
    def enabled(self) -> bool:
        if not super().enabled:
            return False
        if not os.environ.get("TRADE_GOV_API_KEY"):
            log.info("trade_gov.disabled_no_api_key")
            return False
        return True

    async def fetch(
        self, since: datetime, until: datetime
    ) -> AsyncIterator[RawRecord]:
        api_key = os.environ.get("TRADE_GOV_API_KEY", "")
        # CSL search supports ``countries`` as ISO2 list and pagination.
        offset = 0
        page_size = 100
        max_pages = 20  # bounded — CSL is a fixed-size list, not a stream
        for _ in range(max_pages):
            params = {
                "api_key": api_key,
                "size": page_size,
                "offset": offset,
            }
            try:
                response = await self._get(_CSL_URL, params=params)
                payload = response.json()
            except Exception as exc:  # noqa: BLE001
                log.warning("trade_gov.fetch_failed", error=str(exc))
                return

            results = payload.get("results") or []
            if not results:
                return

            for result in results:
                record = _result_to_raw(result)
                if record is None or record.start_date is None:
                    continue
                if not (since <= record.start_date < until):
                    continue
                yield record

            # Pagination: advance until empty page.
            offset += page_size
            if offset >= int(payload.get("total", 0)):
                return

    async def normalize(self, raw: RawRecord) -> Event:
        assert isinstance(raw, TradeGovRawRecord)
        occurred_at = raw.start_date or datetime.now(timezone.utc)
        target_iso3 = _COUNTRY_TO_ISO3.get(raw.country or "")
        dedup_key = f"trade_gov:{raw.entry_id}"
        return Event(
            source="trade_gov",
            occurred_at=occurred_at,
            actor_iso3="USA",  # CSL is a US gov listing
            target_iso3=target_iso3,
            event_type=f"csl_listing_{raw.source_agency.lower().replace(' ', '_')[:32]}",
            domain=EventDomain.economic,
            severity=None,
            payload={
                "_dedup_key": dedup_key,
                "entry_id": raw.entry_id,
                "name": raw.name,
                "source_agency": raw.source_agency,
                "country": raw.country,
                "addresses": raw.addresses,
                "start_date": raw.start_date.isoformat() if raw.start_date else None,
            },
            raw_text=f"Trade.gov CSL: {raw.name} ({raw.source_agency})",
        )
