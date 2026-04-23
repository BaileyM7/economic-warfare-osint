"""Tests for the MarineCadastre AIS adapter."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from wargame_backend.app.db.models import EventDomain
from ingest.marinecadastre_ais import (
    MarineCadastreAISSource,
    MarineCadastreRawRecord,
    _flag_from_mmsi,
    _in_box,
)


class TestHelpers:
    def test_flag_from_mmsi_china(self) -> None:
        assert _flag_from_mmsi("412123456") == "CHN"

    def test_flag_from_mmsi_taiwan(self) -> None:
        assert _flag_from_mmsi("416987654") == "TWN"

    def test_flag_from_mmsi_unknown_prefix(self) -> None:
        assert _flag_from_mmsi("999000000") is None

    def test_in_box(self) -> None:
        box = {
            "lat_min": 21.30,
            "lat_max": 21.40,
            "lon_min": -157.99,
            "lon_max": -157.92,
        }
        assert _in_box(21.35, -157.95, box) is True
        assert _in_box(22.00, -157.95, box) is False


class TestNormalize:
    @pytest.mark.asyncio
    async def test_pearl_harbor_chn_pings(self) -> None:
        raw = MarineCadastreRawRecord(
            date="2026-04-15",
            base="PearlHarbor",
            flag_iso3="CHN",
            ping_count=7,
        )
        event = await MarineCadastreAISSource().normalize(raw)
        assert event.actor_iso3 == "CHN"
        assert event.target_iso3 == "USA"
        assert event.domain is EventDomain.kinetic_limited
        assert event.payload["_dedup_key"] == (
            "marinecadastre_ais:2026-04-15:PearlHarbor:CHN"
        )
