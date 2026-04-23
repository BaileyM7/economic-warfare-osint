"""Tests for the SEC EDGAR adapter."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from wargame_backend.app.db.models import EventDomain
from ingest.sec_edgar import (
    SECEdgarRawRecord,
    SECEdgarSource,
    _parse_filing_date,
)


class TestParsing:
    def test_parse_filing_date(self) -> None:
        assert _parse_filing_date("2026-04-15") == datetime(
            2026, 4, 15, tzinfo=timezone.utc
        )
        assert _parse_filing_date(None) is None
        assert _parse_filing_date("garbage") is None


class TestNormalize:
    @pytest.mark.asyncio
    async def test_8k_filing_with_twn_target(self) -> None:
        raw = SECEdgarRawRecord(
            accession="0001234567-26-000123",
            form_type="8-K",
            filing_date=datetime(2026, 4, 15, tzinfo=timezone.utc),
            company_name="TSMC ADR",
            cik="0001046179",
            keyword='"TSMC"',
            target_iso3="TWN",
        )
        event = await SECEdgarSource().normalize(raw)
        assert event.actor_iso3 == "USA"
        assert event.target_iso3 == "TWN"
        assert event.domain is EventDomain.economic
        assert event.payload["_dedup_key"] == "sec_edgar:0001234567-26-000123"
        assert event.event_type == "edgar_filing_8_k"
