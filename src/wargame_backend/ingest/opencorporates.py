"""OpenCorporates company-registry adapter.

Data source
-----------
OpenCorporates exposes corporate filings at
``https://api.opencorporates.com/v0.4/companies/{jurisdiction}``.  We pull
recently incorporated or modified companies for each slice country's
jurisdiction and emit one Event per company.

Auth
----
Requires ``OPENCORPORATES_API_KEY``.  When unset the adapter disables
itself; OpenCorporates' anonymous tier is too restrictive (50/day) for
production use.

Dedup key
---------
``opencorporates:{jurisdiction}:{company_number}:{updated_date}``
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

_API_BASE = "https://api.opencorporates.com/v0.4/companies"

# OpenCorporates uses ISO-3166-2-style jurisdiction codes (lowercase, may
# include sub-jurisdictions; we use top-level only).
_ISO3_TO_JURISDICTION: dict[str, str] = {
    "CHN": "cn", "TWN": "tw", "USA": "us", "JPN": "jp", "KOR": "kr",
    "PHL": "ph", "AUS": "au", "PRK": "kp", "RUS": "ru", "IND": "in",
}


class OpenCorporatesRawRecord(BaseModel):
    """Typed representation of one company-registry record."""

    company_number: str
    name: str
    jurisdiction: str
    iso3: str
    company_type: str | None = None
    incorporation_date: datetime | None = None
    updated_at: datetime | None = None
    raw: dict[str, Any] = Field(default_factory=dict)


def _parse_date(text: str | None) -> datetime | None:
    if not text:
        return None
    try:
        return datetime.strptime(text[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


class OpenCorporatesSource(Source):
    """OpenCorporates registry adapter — emits one Event per recent company."""

    name: ClassVar[str] = "opencorporates"
    display_name: ClassVar[str] = "OpenCorporates"

    @property
    def enabled(self) -> bool:
        if not super().enabled:
            return False
        if not os.environ.get("OPENCORPORATES_API_KEY"):
            log.info("opencorporates.disabled_no_api_key")
            return False
        return True

    async def fetch(
        self, since: datetime, until: datetime
    ) -> AsyncIterator[RawRecord]:
        api_key = os.environ.get("OPENCORPORATES_API_KEY", "")
        for iso3, jurisdiction in _ISO3_TO_JURISDICTION.items():
            params = {
                "api_token": api_key,
                "incorporation_date_from": since.strftime("%Y-%m-%d"),
                "incorporation_date_to": until.strftime("%Y-%m-%d"),
                "per_page": 100,
            }
            url = f"{_API_BASE}/{jurisdiction}/search"
            try:
                response = await self._get(url, params=params)
                payload = response.json()
            except Exception as exc:  # noqa: BLE001
                log.warning(
                    "opencorporates.fetch_failed",
                    jurisdiction=jurisdiction,
                    error=str(exc),
                )
                continue

            results = (payload.get("results") or {}).get("companies") or []
            for entry in results:
                company = entry.get("company") or {}
                company_number = company.get("company_number")
                if not company_number:
                    continue
                yield OpenCorporatesRawRecord(
                    company_number=str(company_number),
                    name=str(company.get("name") or "(unnamed)"),
                    jurisdiction=jurisdiction,
                    iso3=iso3,
                    company_type=company.get("company_type"),
                    incorporation_date=_parse_date(company.get("incorporation_date")),
                    updated_at=_parse_date(company.get("updated_at")),
                    raw={
                        "company_number": company_number,
                        "name": company.get("name"),
                        "jurisdiction": jurisdiction,
                        "incorporation_date": company.get("incorporation_date"),
                    },
                )

    async def normalize(self, raw: RawRecord) -> Event:
        assert isinstance(raw, OpenCorporatesRawRecord)
        occurred_at = (
            raw.incorporation_date or raw.updated_at or datetime.now(timezone.utc)
        )
        date_suffix = occurred_at.date().isoformat()
        dedup_key = (
            f"opencorporates:{raw.jurisdiction}:{raw.company_number}:{date_suffix}"
        )
        return Event(
            source="opencorporates",
            occurred_at=occurred_at,
            actor_iso3=raw.iso3,
            target_iso3=None,
            event_type="company_registry_update",
            domain=EventDomain.economic,
            severity=None,
            payload={
                "_dedup_key": dedup_key,
                "company_number": raw.company_number,
                "name": raw.name,
                "jurisdiction": raw.jurisdiction,
                "iso3": raw.iso3,
                "company_type": raw.company_type,
                "incorporation_date": (
                    raw.incorporation_date.isoformat()
                    if raw.incorporation_date
                    else None
                ),
            },
            raw_text=f"OpenCorporates {raw.jurisdiction} {raw.company_number}: {raw.name}",
        )
