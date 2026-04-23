"""GLEIF (Global Legal Entity Identifier Foundation) registry adapter.

Data source
-----------
GLEIF's REST API at ``https://api.gleif.org/api/v1/lei-records`` returns
LEI records filterable by country and last-update date.  We pull recently
updated LEIs for each slice country and emit one Event per record.

Auth
----
None.

Dedup key
---------
``gleif:{lei}:{updated_at}`` — re-emitting on each subsequent update is
intentional; the date suffix lets the agent distinguish "still active"
from "freshly modified".
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

_API_URL = "https://api.gleif.org/api/v1/lei-records"

# GLEIF uses ISO2 country codes for the legal-jurisdiction filter.
_ISO3_TO_ISO2: dict[str, str] = {
    "CHN": "CN", "TWN": "TW", "USA": "US", "JPN": "JP", "KOR": "KR",
    "PHL": "PH", "AUS": "AU", "PRK": "KP", "RUS": "RU", "IND": "IN",
}


class GLEIFRawRecord(BaseModel):
    """Typed representation of one LEI record."""

    lei: str
    legal_name: str
    iso3: str
    legal_form: str | None = None
    status: str | None = None
    updated_at: datetime | None = None
    raw: dict[str, Any] = Field(default_factory=dict)


def _parse_iso(text: str | None) -> datetime | None:
    if not text:
        return None
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


class GLEIFSource(Source):
    """GLEIF LEI registry adapter — emits one Event per recently-updated LEI."""

    name: ClassVar[str] = "gleif"
    display_name: ClassVar[str] = "GLEIF LEI Registry"

    async def fetch(
        self, since: datetime, until: datetime
    ) -> AsyncIterator[RawRecord]:
        for iso3, iso2 in _ISO3_TO_ISO2.items():
            # GLEIF's JSON:API implementation does NOT accept nested-operator
            # filters like filter[lastUpdateDate][gte].  We pull the most
            # recently updated LEIs via sort + page[size] and filter the
            # window client-side in normalize / fetch below.
            params = {
                "filter[entity.legalAddress.country]": iso2,
                "page[size]": 100,
                "sort": "-registration.lastUpdateDate",
            }
            try:
                response = await self._get(_API_URL, params=params)
                payload = response.json()
            except Exception as exc:  # noqa: BLE001
                log.warning("gleif.fetch_failed", iso2=iso2, error=str(exc))
                continue

            for record in payload.get("data") or []:
                lei = record.get("id")
                attrs = record.get("attributes") or {}
                entity = attrs.get("entity") or {}
                if not lei:
                    continue
                updated_at = _parse_iso(attrs.get("registration", {}).get("lastUpdateDate"))
                # Window-filter client-side (GLEIF's JSON:API doesn't support
                # ranged filters on this field).  Records with no timestamp
                # are rare — we still emit them tagged as "now" in normalize.
                if updated_at is not None and not (since <= updated_at < until):
                    continue
                yield GLEIFRawRecord(
                    lei=str(lei),
                    legal_name=str(entity.get("legalName", {}).get("name") or "(unnamed)"),
                    iso3=iso3,
                    legal_form=(entity.get("legalForm") or {}).get("id"),
                    status=(attrs.get("registration") or {}).get("status"),
                    updated_at=updated_at,
                    raw={
                        "lei": lei,
                        "legal_name": entity.get("legalName"),
                        "country": iso2,
                    },
                )

    async def normalize(self, raw: RawRecord) -> Event:
        assert isinstance(raw, GLEIFRawRecord)
        occurred_at = raw.updated_at or datetime.now(timezone.utc)
        date_suffix = occurred_at.date().isoformat()
        dedup_key = f"gleif:{raw.lei}:{date_suffix}"
        return Event(
            source="gleif",
            occurred_at=occurred_at,
            actor_iso3=raw.iso3,
            target_iso3=None,
            event_type="lei_record_update",
            domain=EventDomain.economic,
            severity=None,
            payload={
                "_dedup_key": dedup_key,
                "lei": raw.lei,
                "legal_name": raw.legal_name,
                "iso3": raw.iso3,
                "legal_form": raw.legal_form,
                "status": raw.status,
                "updated_at": occurred_at.isoformat(),
            },
            raw_text=f"GLEIF {raw.lei}: {raw.legal_name} ({raw.iso3})",
        )
