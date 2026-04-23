"""Tests for the yfinance adapter."""

from __future__ import annotations

import pytest

from wargame_backend.app.db.models import EventDomain
from ingest.yfinance import YFinanceRawRecord, YFinanceSource, _safe_pct_change


class TestHelpers:
    def test_safe_pct_change(self) -> None:
        assert _safe_pct_change(105.0, 100.0) == 5.0
        assert _safe_pct_change(95.0, 100.0) == -5.0
        assert _safe_pct_change(100.0, 0.0) is None
        assert _safe_pct_change(100.0, None) is None


class TestNormalize:
    @pytest.mark.asyncio
    async def test_market_close_event(self) -> None:
        raw = YFinanceRawRecord(
            ticker="TSM",
            label="TSMC ADR",
            iso3="TWN",
            date="2026-04-15",
            close=140.50,
            prior_close=146.20,
            pct_change=-3.899,
        )
        event = await YFinanceSource().normalize(raw)
        assert event.actor_iso3 == "TWN"
        assert event.target_iso3 is None
        assert event.domain is EventDomain.economic
        assert event.payload["_dedup_key"] == "yfinance:TSM:2026-04-15"
        assert event.payload["pct_change"] == -3.899
        assert event.event_type == "market_close_tsm"

    @pytest.mark.asyncio
    async def test_currency_pair_event(self) -> None:
        raw = YFinanceRawRecord(
            ticker="USDTWD=X",
            label="USD/TWD",
            iso3="TWN",
            date="2026-04-15",
            close=33.50,
            prior_close=33.10,
            pct_change=1.21,
        )
        event = await YFinanceSource().normalize(raw)
        assert event.event_type == "market_close_usdtwd_x"
