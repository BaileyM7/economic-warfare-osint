"""Tests for the UN Comtrade adapter — normalize path + helpers."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from wargame_backend.app.db.models import EventDomain
from ingest.un_comtrade import (
    ComtradeRawRecord,
    UNComtradeSource,
    _periods_in_window,
    _previous_period,
    _safe_pct_change,
)


class TestHelpers:
    def test_previous_period_wraps_year(self) -> None:
        assert _previous_period("202501") == "202412"
        assert _previous_period("202502") == "202501"

    def test_periods_in_window_inclusive(self) -> None:
        since = datetime(2025, 1, 15, tzinfo=timezone.utc)
        until = datetime(2025, 4, 1, tzinfo=timezone.utc)
        assert _periods_in_window(since, until) == ["202501", "202502", "202503"]

    def test_safe_pct_change_handles_zero_prior(self) -> None:
        assert _safe_pct_change(100.0, 0.0) is None
        assert _safe_pct_change(100.0, None) is None
        assert _safe_pct_change(120.0, 100.0) == 20.0
        assert _safe_pct_change(80.0, 100.0) == -20.0


class TestNormalize:
    @pytest.mark.asyncio
    async def test_export_attributes_actor_to_reporter(self) -> None:
        raw = ComtradeRawRecord(
            reporter_iso3="CHN",
            partner_iso3="TWN",
            period="202503",
            commodity_code="85",
            commodity_label="electrical machinery",
            flow="X",
            trade_value_usd=1_000_000_000.0,
            prior_trade_value_usd=1_220_000_000.0,
            mom_pct_change=-18.03,
        )
        event = await UNComtradeSource().normalize(raw)
        assert event.actor_iso3 == "CHN"
        assert event.target_iso3 == "TWN"
        assert event.domain is EventDomain.economic
        assert event.payload["mom_pct_change"] == -18.03
        assert event.payload["commodity"] == "electrical machinery"
        assert event.payload["_dedup_key"] == "comtrade:CHN:TWN:202503:85:X"

    @pytest.mark.asyncio
    async def test_import_inverts_actor_target(self) -> None:
        raw = ComtradeRawRecord(
            reporter_iso3="USA",
            partner_iso3="CHN",
            period="202503",
            commodity_code="85",
            commodity_label="electrical machinery",
            flow="M",
            trade_value_usd=4_000_000_000.0,
            prior_trade_value_usd=3_800_000_000.0,
            mom_pct_change=5.26,
        )
        event = await UNComtradeSource().normalize(raw)
        assert event.actor_iso3 == "CHN"
        assert event.target_iso3 == "USA"

    def test_disabled_when_api_key_missing(self, monkeypatch) -> None:
        monkeypatch.delenv("UN_COMTRADE_KEY", raising=False)
        assert UNComtradeSource().enabled is False

    def test_enabled_when_api_key_present(self, monkeypatch) -> None:
        monkeypatch.setenv("UN_COMTRADE_KEY", "fake-key")
        assert UNComtradeSource().enabled is True
