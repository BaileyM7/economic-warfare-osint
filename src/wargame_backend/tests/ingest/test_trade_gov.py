"""Tests for the Trade.gov CSL adapter."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from wargame_backend.app.db.models import EventDomain
from ingest.trade_gov import (
    TradeGovRawRecord,
    TradeGovSource,
    _parse_date,
    _result_to_raw,
)


class TestParsing:
    def test_parse_date_iso(self) -> None:
        assert _parse_date("2026-04-15") == datetime(
            2026, 4, 15, tzinfo=timezone.utc
        )

    def test_result_to_raw_extracts_country_from_addresses(self) -> None:
        record = _result_to_raw(
            {
                "id": "csl-001",
                "name": "Sanctioned Co.",
                "source": "Entity List (EL)",
                "addresses": [{"country": "China"}],
                "start_date": "2026-04-15",
            }
        )
        assert record is not None
        assert record.entry_id == "csl-001"
        assert record.country == "China"
        assert record.source_agency == "Entity List (EL)"


class TestNormalize:
    @pytest.mark.asyncio
    async def test_china_targeted(self) -> None:
        raw = TradeGovRawRecord(
            entry_id="csl-001",
            name="Sanctioned Co.",
            source_agency="Entity List (EL)",
            country="China",
            start_date=datetime(2026, 4, 15, tzinfo=timezone.utc),
        )
        event = await TradeGovSource().normalize(raw)
        assert event.actor_iso3 == "USA"
        assert event.target_iso3 == "CHN"
        assert event.domain is EventDomain.economic
        assert event.payload["_dedup_key"] == "trade_gov:csl-001"

    def test_disabled_when_api_key_missing(self, monkeypatch) -> None:
        monkeypatch.delenv("TRADE_GOV_API_KEY", raising=False)
        assert TradeGovSource().enabled is False

    def test_enabled_when_api_key_present(self, monkeypatch) -> None:
        monkeypatch.setenv("TRADE_GOV_API_KEY", "fake-key")
        assert TradeGovSource().enabled is True
