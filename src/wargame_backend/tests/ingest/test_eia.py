"""Tests for the EIA adapter — normalize path + helpers."""

from __future__ import annotations

import pytest

from wargame_backend.app.db.models import EventDomain
from ingest.eia import EIARawRecord, EIASource, _safe_float


class TestHelpers:
    def test_safe_float(self) -> None:
        assert _safe_float(None) is None
        assert _safe_float("3.14") == 3.14
        assert _safe_float("not a number") is None
        assert _safe_float(42) == 42.0


class TestNormalize:
    @pytest.mark.asyncio
    async def test_observation_event(self) -> None:
        raw = EIARawRecord(
            series_id="PET.RWTC.D",
            series_label="WTI crude spot",
            period="2026-04-15",
            value=82.50,
            prior_value=78.20,
            delta=4.30,
        )
        event = await EIASource().normalize(raw)
        assert event.actor_iso3 == "USA"
        assert event.domain is EventDomain.economic
        assert event.payload["series_id"] == "PET.RWTC.D"
        assert event.payload["delta"] == 4.30
        assert event.payload["_dedup_key"] == "eia:PET.RWTC.D:2026-04-15"
        assert event.event_type == "energy_observation_pet_rwtc_d"

    def test_disabled_when_api_key_missing(self, monkeypatch) -> None:
        monkeypatch.delenv("EIA_API_KEY", raising=False)
        assert EIASource().enabled is False

    def test_enabled_when_api_key_present(self, monkeypatch) -> None:
        monkeypatch.setenv("EIA_API_KEY", "fake-key")
        assert EIASource().enabled is True
