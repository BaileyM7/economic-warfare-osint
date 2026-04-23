"""Tests for the OpenSanctions adapter — hit parsing + normalize path."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from wargame_backend.app.db.models import EventDomain
from ingest.open_sanctions import (
    OpenSanctionsRawRecord,
    OpenSanctionsSource,
    _hit_to_raw,
    _parse_iso_dt,
)


class TestParsing:
    def test_iso_dt_parses_z_suffix(self) -> None:
        dt = _parse_iso_dt("2026-04-15T12:00:00Z")
        assert dt == datetime(2026, 4, 15, 12, 0, 0, tzinfo=timezone.utc)

    def test_hit_to_raw_extracts_fields(self) -> None:
        hit = {
            "id": "ent-123",
            "schema": "Organization",
            "caption": "Sanctioned Co",
            "properties": {
                "name": ["Sanctioned Co Ltd."],
                "country": ["CN", "HK"],
            },
            "first_seen": "2026-04-15T08:00:00Z",
            "last_seen": "2026-04-15T08:00:00Z",
            "datasets": ["us_ofac_sdn", "eu_fsf"],
        }
        record = _hit_to_raw(hit)
        assert record is not None
        assert record.entity_id == "ent-123"
        assert record.schema_ == "Organization"
        assert record.name == "Sanctioned Co Ltd."
        assert record.countries == ["cn", "hk"]
        assert record.first_seen == datetime(
            2026, 4, 15, 8, 0, 0, tzinfo=timezone.utc
        )
        assert "us_ofac_sdn" in record.datasets


class TestNormalize:
    @pytest.mark.asyncio
    async def test_china_listed_entity_targets_chn(self) -> None:
        raw = OpenSanctionsRawRecord(
            entity_id="ent-123",
            schema_="Organization",
            name="Sanctioned Co Ltd.",
            countries=["cn"],
            first_seen=datetime(2026, 4, 15, tzinfo=timezone.utc),
            last_seen=datetime(2026, 4, 15, tzinfo=timezone.utc),
            datasets=["us_ofac_sdn"],
        )
        event = await OpenSanctionsSource().normalize(raw)
        assert event.actor_iso3 is None  # OpenSanctions has no single issuer
        assert event.target_iso3 == "CHN"
        assert event.domain is EventDomain.economic
        assert event.payload["_dedup_key"] == "opensanctions:ent-123:2026-04-15"
        assert event.event_type == "sanctions_listing_organization"

    @pytest.mark.asyncio
    async def test_no_slice_country_in_list_leaves_target_none(self) -> None:
        raw = OpenSanctionsRawRecord(
            entity_id="ent-456",
            schema_="Person",
            name="Some Person",
            countries=["lt"],  # Lithuania — not in slice
            first_seen=datetime(2026, 4, 15, tzinfo=timezone.utc),
        )
        event = await OpenSanctionsSource().normalize(raw)
        assert event.target_iso3 is None
