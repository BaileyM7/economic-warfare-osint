"""Tests for the GLEIF adapter."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from wargame_backend.app.db.models import EventDomain
from ingest.gleif import GLEIFRawRecord, GLEIFSource, _parse_iso


class TestHelpers:
    def test_parse_iso(self) -> None:
        assert _parse_iso("2026-04-15T08:00:00Z") == datetime(
            2026, 4, 15, 8, 0, 0, tzinfo=timezone.utc
        )
        assert _parse_iso(None) is None


class TestNormalize:
    @pytest.mark.asyncio
    async def test_lei_record_event(self) -> None:
        raw = GLEIFRawRecord(
            lei="529900T8BM49AURSDO55",
            legal_name="Taiwan Semiconductor Manufacturing Co",
            iso3="TWN",
            legal_form="STOCK",
            status="ISSUED",
            updated_at=datetime(2026, 4, 15, 8, 0, 0, tzinfo=timezone.utc),
        )
        event = await GLEIFSource().normalize(raw)
        assert event.actor_iso3 == "TWN"
        assert event.target_iso3 is None
        assert event.domain is EventDomain.economic
        assert event.event_type == "lei_record_update"
        assert event.payload["_dedup_key"] == (
            "gleif:529900T8BM49AURSDO55:2026-04-15"
        )
