"""OpenSanctions consolidated sanctions / PEP / watchlist adapter.

Data source
-----------
OpenSanctions aggregates 200+ public sanctions, PEP, and watchlist sources
into a single unified dataset.  We hit the public ``/search`` endpoint
filtered by ``schema=Person|Organization|Vessel`` and ``countries=`` for
each slice country in turn, then dedupe by entity ID.

Auth
----
The public API at ``api.opensanctions.org`` is keyless for normal-rate
read access.  ``OPENSANCTIONS_API_KEY`` is optional and only sent when
present (gives higher rate limits on the paid tier).

Dedup key
---------
``opensanctions:{entity_id}:{first_seen_date}`` — re-listed entities under
new programs land as fresh rows; pure re-emission of existing entries is
deduped.
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

_API_BASE = "https://api.opensanctions.org/search/default"

# Country codes OpenSanctions uses (ISO2 lowercase).  Filter is OR-ed
# across these — we want anything tied to any slice country.
_SLICE_ISO2: dict[str, str] = {
    "cn": "CHN",
    "tw": "TWN",
    "us": "USA",
    "jp": "JPN",
    "kr": "KOR",
    "ph": "PHL",
    "au": "AUS",
    "kp": "PRK",
    "ru": "RUS",
    "in": "IND",
}


class OpenSanctionsRawRecord(BaseModel):
    """Typed representation of one OpenSanctions hit."""

    entity_id: str
    schema_: str  # "Person" | "Organization" | "Vessel" | "Asset" …
    name: str
    countries: list[str] = Field(default_factory=list)
    first_seen: datetime | None = None
    last_seen: datetime | None = None
    datasets: list[str] = Field(default_factory=list)
    raw: dict[str, Any] = Field(default_factory=dict)


def _parse_iso_dt(text: str | None) -> datetime | None:
    if not text:
        return None
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        return None


def _hit_to_raw(hit: dict[str, Any]) -> OpenSanctionsRawRecord | None:
    entity_id = hit.get("id")
    if not entity_id:
        return None
    properties = hit.get("properties") or {}
    name_list = properties.get("name") or [hit.get("caption") or ""]
    name = (name_list[0] or "").strip() or "(unnamed)"
    countries = [c.lower() for c in (properties.get("country") or []) if c]

    return OpenSanctionsRawRecord(
        entity_id=str(entity_id),
        schema_=str(hit.get("schema") or "Thing"),
        name=name,
        countries=countries,
        first_seen=_parse_iso_dt(hit.get("first_seen")),
        last_seen=_parse_iso_dt(hit.get("last_seen")),
        datasets=list(hit.get("datasets") or []),
        raw={
            "id": entity_id,
            "schema": hit.get("schema"),
            "datasets": hit.get("datasets"),
            "first_seen": hit.get("first_seen"),
            "last_seen": hit.get("last_seen"),
        },
    )


class OpenSanctionsSource(Source):
    """OpenSanctions adapter — emits one Event per (entity, country) hit."""

    name: ClassVar[str] = "opensanctions"  # match extractor's _SOURCE_KEY
    display_name: ClassVar[str] = "OpenSanctions"

    async def fetch(
        self, since: datetime, until: datetime
    ) -> AsyncIterator[RawRecord]:
        api_key = os.environ.get("OPENSANCTIONS_API_KEY", "")
        headers = {"Authorization": f"ApiKey {api_key}"} if api_key else {}

        seen_ids: set[str] = set()
        for iso2 in _SLICE_ISO2:
            # OpenSanctions' /search endpoint requires a ``q`` term; we use
            # the country ISO2 itself as a broad match and let the country
            # filter narrow it.  Limit pulled down to 100 — plenty per run.
            params = {
                "q": iso2,
                "countries": iso2,
                "limit": 100,
            }
            try:
                response = await self._get(
                    _API_BASE, params=params, headers=headers
                )
                payload = response.json()
            except Exception as exc:  # noqa: BLE001
                log.warning(
                    "opensanctions.fetch_failed", iso2=iso2, error=str(exc)
                )
                continue

            for hit in payload.get("results") or []:
                record = _hit_to_raw(hit)
                if record is None or record.entity_id in seen_ids:
                    continue
                # NOTE: we used to filter by ``first_seen`` here, but
                # OpenSanctions' first_seen dates are typically years old —
                # the feed is a consolidated reference list, not a real-time
                # stream. We now emit every hit and rely on the (entity_id,
                # first_seen.date) dedup key to prevent duplicate rows on
                # repeated runs.  If a jurisdiction's hit count balloons,
                # tighten via the ``limit`` param instead.
                seen_ids.add(record.entity_id)
                yield record

    async def normalize(self, raw: RawRecord) -> Event:
        assert isinstance(raw, OpenSanctionsRawRecord)
        occurred_at = raw.first_seen or datetime.now(timezone.utc)

        # Pick the first slice country in the entity's country list, if any.
        target_iso3: str | None = None
        for c in raw.countries:
            iso3 = _SLICE_ISO2.get(c)
            if iso3 is not None:
                target_iso3 = iso3
                break

        dedup_key = f"opensanctions:{raw.entity_id}:{occurred_at.date().isoformat()}"
        return Event(
            source="opensanctions",
            occurred_at=occurred_at,
            actor_iso3=None,  # OpenSanctions aggregates many issuers; no single actor
            target_iso3=target_iso3,
            event_type=f"sanctions_listing_{raw.schema_.lower()}",
            domain=EventDomain.economic,
            severity=None,
            payload={
                "_dedup_key": dedup_key,
                "entity_id": raw.entity_id,
                "schema": raw.schema_,
                "name": raw.name,
                "countries": raw.countries,
                "datasets": raw.datasets,
                "first_seen": raw.first_seen.isoformat() if raw.first_seen else None,
                "last_seen": raw.last_seen.isoformat() if raw.last_seen else None,
            },
            raw_text=(
                f"OpenSanctions: {raw.name} ({raw.schema_}) "
                f"listed in {len(raw.datasets)} dataset(s)"
            ),
        )
