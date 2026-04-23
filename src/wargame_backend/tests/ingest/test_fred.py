"""Tests for the FRED adapter — normalize path + helpers."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from wargame_backend.app.db.models import EventDomain
from ingest.fred import FREDRawRecord, FREDSource, _safe_float


class TestHelpers:
    def test_safe_float_handles_missing_values(self) -> None:
        assert _safe_float(None) is None
        assert _safe_float("") is None
        assert _safe_float(".") is None  # FRED's missing-value sentinel
        assert _safe_float("NA") is None
        assert _safe_float("4.123") == 4.123
        assert _safe_float("not a number") is None


class TestNormalize:
    @pytest.mark.asyncio
    async def test_observation_with_delta(self) -> None:
        raw = FREDRawRecord(
            series_id="DGS10",
            series_label="US 10Y Treasury yield",
            date="2026-04-15",
            value=4.50,
            prior_value=4.20,
            delta=0.30,
        )
        event = await FREDSource().normalize(raw)
        assert event.actor_iso3 == "USA"
        assert event.target_iso3 is None
        assert event.domain is EventDomain.economic
        assert event.payload["_dedup_key"] == "fred:DGS10:2026-04-15"
        assert event.payload["series_id"] == "DGS10"
        assert event.payload["delta"] == 0.30
        assert event.event_type == "macro_observation_dgs10"

    def test_disabled_when_api_key_missing(self, monkeypatch) -> None:
        monkeypatch.delenv("FRED_API_KEY", raising=False)
        assert FREDSource().enabled is False

    def test_enabled_when_api_key_present(self, monkeypatch) -> None:
        monkeypatch.setenv("FRED_API_KEY", "fake-key")
        assert FREDSource().enabled is True
