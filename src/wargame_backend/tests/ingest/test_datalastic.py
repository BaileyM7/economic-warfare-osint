"""Tests for the Datalastic AIS adapter — normalize path."""

from __future__ import annotations

import pytest

from wargame_backend.app.db.models import EventDomain
from ingest.datalastic import DatalasticRawRecord, DatalasticSource


class TestNormalize:
    @pytest.mark.asyncio
    async def test_zone_presence_event(self) -> None:
        raw = DatalasticRawRecord(
            zone="TWN_strait_buffer",
            flag_iso3="CHN",
            period_iso="2026-04-15T12:00:00+00:00",
            ping_count=42,
            prior_ping_count=14,
            ping_count_w_w_pct=200.0,
            raw_sample={"name": "PRC NAVAL VESSEL", "mmsi": "412123456"},
        )
        event = await DatalasticSource().normalize(raw)
        assert event.actor_iso3 == "CHN"
        assert event.target_iso3 is None
        assert event.domain is EventDomain.kinetic_limited
        assert event.payload["zone"] == "TWN_strait_buffer"
        assert event.payload["ping_count"] == 42
        assert event.payload["ping_count_w_w_pct"] == 200.0
        assert event.payload["_dedup_key"].startswith(
            "datalastic:CHN:TWN_strait_buffer:"
        )

    def test_disabled_when_api_key_missing(self, monkeypatch) -> None:
        monkeypatch.delenv("DATALASTIC_API_KEY", raising=False)
        assert DatalasticSource().enabled is False

    def test_enabled_when_api_key_present(self, monkeypatch) -> None:
        monkeypatch.setenv("DATALASTIC_API_KEY", "fake-key")
        assert DatalasticSource().enabled is True
