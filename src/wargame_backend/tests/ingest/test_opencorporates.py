"""Tests for the OpenCorporates adapter."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from wargame_backend.app.db.models import EventDomain
from ingest.opencorporates import (
    OpenCorporatesRawRecord,
    OpenCorporatesSource,
    _parse_date,
)


class TestHelpers:
    def test_parse_date(self) -> None:
        assert _parse_date("2026-04-15") == datetime(
            2026, 4, 15, tzinfo=timezone.utc
        )
        assert _parse_date(None) is None


class TestNormalize:
    @pytest.mark.asyncio
    async def test_company_event(self) -> None:
        raw = OpenCorporatesRawRecord(
            company_number="91110000123456789X",
            name="China Holdings Co Ltd",
            jurisdiction="cn",
            iso3="CHN",
            company_type="Limited Company",
            incorporation_date=datetime(2026, 4, 15, tzinfo=timezone.utc),
        )
        event = await OpenCorporatesSource().normalize(raw)
        assert event.actor_iso3 == "CHN"
        assert event.domain is EventDomain.economic
        assert event.event_type == "company_registry_update"
        assert event.payload["_dedup_key"] == (
            "opencorporates:cn:91110000123456789X:2026-04-15"
        )

    def test_disabled_when_api_key_missing(self, monkeypatch) -> None:
        monkeypatch.delenv("OPENCORPORATES_API_KEY", raising=False)
        assert OpenCorporatesSource().enabled is False
