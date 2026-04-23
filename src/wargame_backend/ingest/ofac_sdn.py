"""OFAC SDN (Specially Designated Nationals) list adapter.

Data source
-----------
US Treasury OFAC publishes the SDN list as XML at
``https://www.treasury.gov/ofac/downloads/sdn.xml`` (also a CSV mirror).
The list is updated whenever a new designation is published — typically
several times per week.  Our window-based fetch downloads the current XML
and emits one record per ``<sdnEntry>`` whose ``publishInformation/Publish_Date``
falls within ``[since, until)``.

Auth
----
None.  Public US Government feed.

Dedup key
---------
``ofac_sdn:{uid}`` where ``uid`` is the OFAC-assigned numeric identifier.
Re-running the adapter is idempotent: existing entries are skipped, only
new designations land as fresh rows.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import datetime, timezone
from typing import Any, ClassVar
from xml.etree import ElementTree as ET

import structlog
from pydantic import BaseModel, Field

from wargame_backend.app.db.models import Event, EventDomain
from ingest.base import Source, RawRecord

log = structlog.get_logger(__name__)

_OFAC_SDN_URL = "https://www.treasury.gov/ofac/downloads/sdn.xml"
_OFAC_NS = {"ofac": "http://tempuri.org/sdnList.xsd"}

# Country names (as they appear in OFAC <country> text) → ISO3.
# Only the slice countries are mapped; anything else falls through to None
# and is recorded with no actor/target.
_COUNTRY_TO_ISO3: dict[str, str] = {
    "China": "CHN",
    "Hong Kong": "CHN",
    "Macau": "CHN",
    "Taiwan": "TWN",
    "United States": "USA",
    "Japan": "JPN",
    "Korea, South": "KOR",
    "South Korea": "KOR",
    "Korea, North": "PRK",
    "North Korea": "PRK",
    "Philippines": "PHL",
    "Australia": "AUS",
    "Russia": "RUS",
    "India": "IND",
}


class OFACSDNRawRecord(BaseModel):
    """Typed representation of one OFAC SDN entry."""

    uid: str
    name: str
    sdn_type: str  # "Individual" | "Entity" | "Vessel" | "Aircraft"
    country: str | None = None
    program: str | None = None
    publish_date: datetime | None = None
    raw_payload: dict[str, Any] = Field(default_factory=dict)


def _parse_publish_date(text: str | None) -> datetime | None:
    if not text:
        return None
    for fmt in ("%m/%d/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def _entry_to_raw(entry: ET.Element) -> OFACSDNRawRecord | None:
    """Map one <sdnEntry> XML element to an OFACSDNRawRecord."""
    uid_el = entry.find("ofac:uid", _OFAC_NS) or entry.find("uid")
    if uid_el is None or not uid_el.text:
        return None

    last = (entry.find("ofac:lastName", _OFAC_NS) or entry.find("lastName"))
    first = (entry.find("ofac:firstName", _OFAC_NS) or entry.find("firstName"))
    name_parts = [
        (first.text.strip() if first is not None and first.text else ""),
        (last.text.strip() if last is not None and last.text else ""),
    ]
    name = " ".join(p for p in name_parts if p) or "(unnamed)"

    sdn_type_el = entry.find("ofac:sdnType", _OFAC_NS) or entry.find("sdnType")
    sdn_type = (sdn_type_el.text or "").strip() if sdn_type_el is not None else ""

    program_el = (
        entry.find("ofac:programList/ofac:program", _OFAC_NS)
        or entry.find("programList/program")
    )
    program = (program_el.text or "").strip() if program_el is not None else None

    # First listed country, if any (entries can list multiple addresses).
    country_el = (
        entry.find("ofac:addressList/ofac:address/ofac:country", _OFAC_NS)
        or entry.find("addressList/address/country")
    )
    country = (country_el.text or "").strip() if country_el is not None else None

    pub_el = (
        entry.find("ofac:publishInformation/ofac:Publish_Date", _OFAC_NS)
        or entry.find("publishInformation/Publish_Date")
    )
    publish_date = _parse_publish_date(pub_el.text if pub_el is not None else None)

    return OFACSDNRawRecord(
        uid=uid_el.text.strip(),
        name=name,
        sdn_type=sdn_type or "Unknown",
        country=country,
        program=program,
        publish_date=publish_date,
        raw_payload={
            "uid": uid_el.text.strip(),
            "name": name,
            "sdn_type": sdn_type,
            "country": country,
            "program": program,
            "publish_date": pub_el.text if pub_el is not None else None,
        },
    )


class OFACSDNSource(Source):
    """OFAC SDN list adapter — emits one Event per designation in window."""

    name: ClassVar[str] = "ofac_sdn"
    display_name: ClassVar[str] = "OFAC SDN List"

    async def fetch(
        self, since: datetime, until: datetime
    ) -> AsyncIterator[RawRecord]:
        try:
            response = await self._get(_OFAC_SDN_URL)
        except Exception as exc:  # noqa: BLE001
            log.warning("ofac_sdn.fetch_failed", error=str(exc))
            return

        try:
            root = ET.fromstring(response.content)
        except ET.ParseError as exc:
            log.warning("ofac_sdn.parse_failed", error=str(exc))
            return

        # <sdnList><sdnEntry>...</sdnEntry></sdnList>
        for entry in root.iter():
            tag = entry.tag.split("}", 1)[-1]  # strip namespace prefix
            if tag != "sdnEntry":
                continue
            record = _entry_to_raw(entry)
            if record is None:
                continue
            # Filter by window: entries with no publish date are skipped
            # (they pre-date the era we care about).  Entries inside the
            # window or with publish_date >= since are emitted.
            if record.publish_date is None:
                continue
            if not (since <= record.publish_date < until):
                continue
            yield record

    async def normalize(self, raw: RawRecord) -> Event:
        assert isinstance(raw, OFACSDNRawRecord)
        occurred_at = raw.publish_date or datetime.now(timezone.utc)

        target_iso3 = _COUNTRY_TO_ISO3.get(raw.country or "")
        # Treasury (USA) is the actor on every designation.
        actor_iso3 = "USA"

        dedup_key = f"ofac_sdn:{raw.uid}"
        return Event(
            source="ofac_sdn",
            occurred_at=occurred_at,
            actor_iso3=actor_iso3,
            target_iso3=target_iso3,
            event_type=f"sdn_designation_{raw.sdn_type.lower()}",
            domain=EventDomain.economic,
            severity=None,
            payload={
                "_dedup_key": dedup_key,
                "uid": raw.uid,
                "name": raw.name,
                "sdn_type": raw.sdn_type,
                "country": raw.country,
                "program": raw.program,
                "publish_date": (
                    raw.publish_date.isoformat() if raw.publish_date else None
                ),
            },
            raw_text=f"OFAC SDN: {raw.name} ({raw.sdn_type}, {raw.program or '?'})",
        )
