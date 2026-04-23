"""Tests for the World Bank adapter — normalize path and indicator mapping.

VCR cassette: tests/ingest/cassettes/worldbank_gdp_chn.yaml
Contains GDP observations for China (NY.GDP.MKTP.CD, years 2021–2023).
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from wargame_backend.app.db.models import EventDomain
from ingest.worldbank import (
    WorldBankRawRecord,
    WorldBankSource,
    SLICE_ISO3,
    INDICATORS,
    MRV,
)


# ---------------------------------------------------------------------------
# WorldBankSource.normalize
# ---------------------------------------------------------------------------

def _make_wb_raw(**overrides) -> WorldBankRawRecord:
    defaults = dict(
        iso3="CHN",
        indicator_id="NY.GDP.MKTP.CD",
        indicator_label="GDP (current USD)",
        year=2023,
        value=17794782000000.0,
    )
    defaults.update(overrides)
    return WorldBankRawRecord(**defaults)


@pytest.mark.asyncio
async def test_normalize_gdp_indicator():
    """GDP observation → economic domain, actor_iso3=CHN, no target."""
    raw = _make_wb_raw()
    src = WorldBankSource()
    event = await src.normalize(raw)

    assert event.source == "worldbank"
    assert event.domain == EventDomain.economic
    assert event.event_type == "economic_indicator"
    assert event.actor_iso3 == "CHN"
    assert event.target_iso3 is None
    assert event.occurred_at == datetime(2023, 1, 1, tzinfo=timezone.utc)
    assert event.severity is None
    assert event.payload["indicator"] == "NY.GDP.MKTP.CD"
    assert event.payload["value"] == pytest.approx(17794782000000.0)
    assert event.payload["year"] == 2023


@pytest.mark.asyncio
async def test_normalize_dedup_key():
    """Dedup key includes iso3, indicator, and year for idempotency."""
    raw = _make_wb_raw(iso3="TWN", indicator_id="SP.POP.TOTL", year=2022, value=23570000.0)
    src = WorldBankSource()
    event = await src.normalize(raw)
    assert event.payload["_dedup_key"] == "worldbank:TWN:SP.POP.TOTL:2022"


@pytest.mark.asyncio
async def test_normalize_null_value():
    """None value is stored in payload without raising."""
    raw = _make_wb_raw(iso3="PRK", value=None, year=2020)
    src = WorldBankSource()
    event = await src.normalize(raw)
    assert event.payload["value"] is None
    assert "N/A" in (event.raw_text or "")


@pytest.mark.asyncio
async def test_normalize_raw_text_format():
    """raw_text should include country, indicator label, value, and year."""
    raw = _make_wb_raw(iso3="USA", indicator_label="GDP (current USD)", value=27360000000000.0, year=2023)
    src = WorldBankSource()
    event = await src.normalize(raw)
    assert "USA" in (event.raw_text or "")
    assert "GDP" in (event.raw_text or "")
    assert "2023" in (event.raw_text or "")


@pytest.mark.asyncio
async def test_normalize_year_zero_fallback():
    """year=0 should not crash; falls back to now."""
    raw = _make_wb_raw(year=0)
    src = WorldBankSource()
    event = await src.normalize(raw)
    assert event.occurred_at is not None


# ---------------------------------------------------------------------------
# Slice coverage
# ---------------------------------------------------------------------------

def test_all_slice_countries_covered():
    """SLICE_ISO3 must cover all 10 vertical-slice countries."""
    expected = {"CHN", "TWN", "USA", "JPN", "KOR", "PHL", "AUS", "PRK", "RUS", "IND"}
    assert set(SLICE_ISO3) == expected


def test_all_four_indicators_present():
    """All four economic indicators must be in the INDICATORS list."""
    ids = {ind_id for ind_id, _ in INDICATORS}
    assert "NY.GDP.MKTP.CD" in ids
    assert "NE.RSB.GNFS.CD" in ids
    assert "MS.MIL.XPND.GD.ZS" in ids
    assert "SP.POP.TOTL" in ids


# ---------------------------------------------------------------------------
# fetch — mock HTTP responses
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_fetch_yields_records_from_mock_response():
    """fetch() yields WorldBankRawRecord objects when HTTP returns valid JSON."""
    mock_response = MagicMock()
    mock_response.json.return_value = [
        {"page": 1, "pages": 1, "per_page": 5, "total": 2},
        [
            {
                "indicator": {"id": "NY.GDP.MKTP.CD", "value": "GDP (current US$)"},
                "country": {"id": "CN", "value": "China"},
                "countryiso3code": "CHN",
                "date": "2023",
                "value": 17794782000000.0,
            },
            {
                "indicator": {"id": "NY.GDP.MKTP.CD", "value": "GDP (current US$)"},
                "country": {"id": "CN", "value": "China"},
                "countryiso3code": "CHN",
                "date": "2022",
                "value": 17963170000000.0,
            },
        ],
    ]

    src = WorldBankSource()

    with patch.object(src, "_get", return_value=mock_response) as mock_get:
        records = []
        since = datetime(2023, 1, 1, tzinfo=timezone.utc)
        until = datetime(2024, 1, 1, tzinfo=timezone.utc)
        async for rec in src.fetch(since, until):
            records.append(rec)
            # Only fetch first indicator for first country in test
            if len(records) >= 2:
                break

    # Should have yielded WorldBankRawRecord instances
    assert len(records) >= 2
    assert records[0].iso3 == "CHN"
    assert records[0].indicator_id == "NY.GDP.MKTP.CD"
    assert records[0].year == 2023


@pytest.mark.asyncio
async def test_fetch_handles_null_data_gracefully():
    """fetch() should skip indicators that return null data array."""
    mock_response = MagicMock()
    mock_response.json.return_value = [
        {"page": 1, "pages": 1, "per_page": 5, "total": 0},
        None,  # WB returns null for countries with no data
    ]

    src = WorldBankSource()
    with patch.object(src, "_get", return_value=mock_response):
        records = []
        since = datetime(2023, 1, 1, tzinfo=timezone.utc)
        until = datetime(2024, 1, 1, tzinfo=timezone.utc)
        async for rec in src.fetch(since, until):
            records.append(rec)

    # PRK (North Korea) commonly has null data — should not crash
    assert isinstance(records, list)


# ---------------------------------------------------------------------------
# VCR cassette replay placeholder
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_fetch_with_vcr_cassette():
    """Replay World Bank API response from cassette (skips if cassette absent)."""
    cassette = Path(__file__).parent / "cassettes" / "worldbank_gdp_chn.yaml"
    if not cassette.exists():
        pytest.skip("VCR cassette worldbank_gdp_chn.yaml not present")
    pytest.skip("Integrate pytest-recording to replay cassette automatically")
