"""ICIJ Offshore Leaks adapter.

Data source
-----------
The International Consortium of Investigative Journalists publishes a
combined Offshore Leaks dataset (Panama Papers, Pandora Papers, Paradise
Papers, Bahamas Leaks, Offshore Leaks 2013) at
``https://offshoreleaks.icij.org/search``.  The site exposes a JSON
search endpoint we hit by jurisdiction.

Auth
----
None.  Hosted as a public investigative dataset.

This is a *static* leak archive — once a record exists in the database
its publication date does not move.  We dedupe by (node_id, source) and
re-emission on subsequent runs is harmless (skipped via dedup_key).

Dedup key
---------
``icij_offshore:{node_id}``
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

_SEARCH_URL = "https://offshoreleaks.icij.org/search"

# ICIJ uses ISO3-style "jurisdictions" inconsistently across leaks; the
# search endpoint accepts ISO2-ish 2-3 letter codes.  These match ICIJ's
# slugs as observed in their public UI.
_ISO3_TO_ICIJ: dict[str, str] = {
    "CHN": "CHN", "TWN": "TWN", "USA": "USA", "JPN": "JPN", "KOR": "KOR",
    "PHL": "PHL", "AUS": "AUS", "PRK": "PRK", "RUS": "RUS", "IND": "IND",
}


class ICIJOffshoreRawRecord(BaseModel):
    """Typed representation of one ICIJ offshore-leaks entity."""

    node_id: str
    name: str
    iso3: str
    jurisdiction: str | None = None
    leak_source: str | None = None  # "Panama Papers", "Pandora Papers", …
    incorporation_date: datetime | None = None
    raw: dict[str, Any] = Field(default_factory=dict)


def _parse_date(text: str | None) -> datetime | None:
    if not text:
        return None
    for fmt in ("%Y-%m-%d", "%d-%b-%Y"):
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


class ICIJOffshoreSource(Source):
    """ICIJ Offshore Leaks adapter — emits one Event per matched entity."""

    name: ClassVar[str] = "icij_offshore"
    display_name: ClassVar[str] = "ICIJ Offshore Leaks"

    async def fetch(
        self, since: datetime, until: datetime
    ) -> AsyncIterator[RawRecord]:
        # ICIJ publishes a static dataset — the time window only filters by
        # incorporation_date, not by ingest time.  We pull all entities
        # whose incorporation_date falls in [since, until).
        for iso3, icij_code in _ISO3_TO_ICIJ.items():
            params = {
                "q": "",
                "c": icij_code,  # country filter
                "j": icij_code,  # jurisdiction filter
                "format": "json",
            }
            try:
                response = await self._get(_SEARCH_URL, params=params)
                payload = response.json()
            except Exception as exc:  # noqa: BLE001
                log.warning("icij_offshore.fetch_failed", iso3=iso3, error=str(exc))
                continue

            for hit in payload.get("results") or []:
                node_id = hit.get("node_id") or hit.get("id")
                if not node_id:
                    continue
                inc_date = _parse_date(hit.get("incorporation_date"))
                if inc_date is None:
                    continue
                if not (since <= inc_date < until):
                    continue
                yield ICIJOffshoreRawRecord(
                    node_id=str(node_id),
                    name=str(hit.get("name") or hit.get("entity") or "(unnamed)"),
                    iso3=iso3,
                    jurisdiction=hit.get("jurisdiction"),
                    leak_source=hit.get("source") or hit.get("leak"),
                    incorporation_date=inc_date,
                    raw={
                        "node_id": node_id,
                        "name": hit.get("name"),
                        "jurisdiction": hit.get("jurisdiction"),
                        "source": hit.get("source"),
                    },
                )

    async def normalize(self, raw: RawRecord) -> Event:
        assert isinstance(raw, ICIJOffshoreRawRecord)
        occurred_at = raw.incorporation_date or datetime.now(timezone.utc)
        dedup_key = f"icij_offshore:{raw.node_id}"
        return Event(
            source="icij_offshore",
            occurred_at=occurred_at,
            actor_iso3=raw.iso3,
            target_iso3=None,
            event_type=(
                f"offshore_entity_"
                f"{(raw.leak_source or 'unknown').lower().replace(' ', '_')[:32]}"
            ),
            domain=EventDomain.economic,
            severity=None,
            payload={
                "_dedup_key": dedup_key,
                "node_id": raw.node_id,
                "name": raw.name,
                "iso3": raw.iso3,
                "jurisdiction": raw.jurisdiction,
                "leak_source": raw.leak_source,
                "incorporation_date": (
                    raw.incorporation_date.isoformat()
                    if raw.incorporation_date
                    else None
                ),
            },
            raw_text=(
                f"ICIJ {raw.leak_source or 'leak'}: {raw.name} "
                f"({raw.jurisdiction or raw.iso3})"
            ),
        )
