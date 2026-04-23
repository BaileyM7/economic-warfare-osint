"""Tests for the OFAC SDN adapter — XML parsing + normalize path."""

from __future__ import annotations

from datetime import datetime, timezone
from xml.etree import ElementTree as ET

import pytest

from wargame_backend.app.db.models import EventDomain
from ingest.ofac_sdn import (
    OFACSDNRawRecord,
    OFACSDNSource,
    _entry_to_raw,
    _parse_publish_date,
)

# Minimal SDN XML fixture — namespace omitted on purpose; the parser
# tolerates both namespaced and bare element names.
_SAMPLE_XML = """\
<sdnList>
  <sdnEntry>
    <uid>12345</uid>
    <firstName>Acme</firstName>
    <lastName>Holdings Ltd.</lastName>
    <sdnType>Entity</sdnType>
    <programList>
      <program>SDGT</program>
    </programList>
    <addressList>
      <address>
        <country>China</country>
      </address>
    </addressList>
    <publishInformation>
      <Publish_Date>03/15/2026</Publish_Date>
    </publishInformation>
  </sdnEntry>
</sdnList>
"""


class TestParsing:
    def test_parses_publish_date_in_known_formats(self) -> None:
        assert _parse_publish_date("03/15/2026") == datetime(
            2026, 3, 15, tzinfo=timezone.utc
        )
        assert _parse_publish_date("2026-03-15") == datetime(
            2026, 3, 15, tzinfo=timezone.utc
        )
        assert _parse_publish_date(None) is None
        assert _parse_publish_date("not a date") is None

    def test_entry_to_raw_extracts_fields(self) -> None:
        root = ET.fromstring(_SAMPLE_XML)
        entry = next(
            e for e in root.iter() if e.tag.split("}", 1)[-1] == "sdnEntry"
        )
        record = _entry_to_raw(entry)
        assert record is not None
        assert record.uid == "12345"
        assert record.name == "Acme Holdings Ltd."
        assert record.sdn_type == "Entity"
        assert record.country == "China"
        assert record.program == "SDGT"
        assert record.publish_date == datetime(2026, 3, 15, tzinfo=timezone.utc)


class TestNormalize:
    @pytest.mark.asyncio
    async def test_china_country_maps_to_chn_target(self) -> None:
        raw = OFACSDNRawRecord(
            uid="12345",
            name="Acme Holdings Ltd.",
            sdn_type="Entity",
            country="China",
            program="SDGT",
            publish_date=datetime(2026, 3, 15, tzinfo=timezone.utc),
        )
        event = await OFACSDNSource().normalize(raw)
        assert event.actor_iso3 == "USA"  # Treasury is the actor
        assert event.target_iso3 == "CHN"
        assert event.domain is EventDomain.economic
        assert event.payload["_dedup_key"] == "ofac_sdn:12345"
        assert event.payload["program"] == "SDGT"
        assert event.event_type == "sdn_designation_entity"

    @pytest.mark.asyncio
    async def test_unknown_country_leaves_target_none(self) -> None:
        raw = OFACSDNRawRecord(
            uid="999",
            name="Unknown Entity",
            sdn_type="Entity",
            country="Lichtenstein",
            program="SDGT",
            publish_date=datetime(2026, 3, 15, tzinfo=timezone.utc),
        )
        event = await OFACSDNSource().normalize(raw)
        assert event.target_iso3 is None
