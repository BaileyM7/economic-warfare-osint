"""Tests for the IMF SDMX adapter."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from wargame_backend.app.db.models import EventDomain
from ingest.imf import IMFRawRecord, IMFSource, _period_to_dt


class TestHelpers:
    def test_period_to_dt_yearly(self) -> None:
        assert _period_to_dt("2026") == datetime(2026, 1, 1, tzinfo=timezone.utc)

    def test_period_to_dt_quarterly(self) -> None:
        assert _period_to_dt("2026-Q2") == datetime(
            2026, 4, 1, tzinfo=timezone.utc
        )
        assert _period_to_dt("2026-Q4") == datetime(
            2026, 10, 1, tzinfo=timezone.utc
        )


class TestNormalize:
    @pytest.mark.asyncio
    async def test_observation_event(self) -> None:
        raw = IMFRawRecord(
            dataset="IFS",
            indicator="RAFA_USD",
            indicator_label="Reserve assets (USD)",
            iso3="CHN",
            period="2026-Q1",
            value=3_100_000_000_000.0,
        )
        event = await IMFSource().normalize(raw)
        assert event.actor_iso3 == "CHN"
        assert event.target_iso3 is None
        assert event.domain is EventDomain.economic
        assert event.payload["_dedup_key"] == "imf:IFS:RAFA_USD:CHN:2026-Q1"
        assert event.payload["value"] == 3_100_000_000_000.0
