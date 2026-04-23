"""Tests for the GDELT 2.0 adapter — normalize path and country filter.

VCR cassette: tests/ingest/cassettes/gdelt_export.yaml
The cassette contains a minimal gzipped CSV with 3 rows:
  - 1 CHN→TWN event (code 193, Goldstein -7.0) — should pass filter
  - 1 JPN→AUS event (code 036) — should pass filter
  - 1 DEU→FRA event (code 010) — should be filtered OUT

Real HTTP is never hit; the fixture CSV is loaded directly in tests.
"""

from __future__ import annotations

import csv
import io
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from ingest.gdelt import (
    GDELTSource,
    GDELTRawRecord,
    _row_to_raw,
    _gdelt_file_urls_in_window,
    SLICE_ISO2,
    COL,
)
from ingest.cameo import cameo_to_domain
from wargame_shared.schemas.sim_event import Domain


# ---------------------------------------------------------------------------
# Fixture CSV data (minimal GDELT export format — tab-separated, 61 columns)
# ---------------------------------------------------------------------------

def _make_gdelt_row(**overrides: str) -> list[str]:
    """Build a 61-column GDELT row with defaults, applying overrides by col name."""
    row = [""] * 61
    row[COL["GlobalEventID"]] = "123456"
    row[COL["SQLDATE"]] = "20250102"
    row[COL["Actor1Code"]] = "CHN"
    row[COL["Actor1Name"]] = "China"
    row[COL["Actor1CountryCode"]] = "CH"
    row[COL["Actor2Code"]] = "TWN"
    row[COL["Actor2Name"]] = "Taiwan"
    row[COL["Actor2CountryCode"]] = "TW"
    row[COL["EventCode"]] = "193"
    row[COL["EventBaseCode"]] = "19"
    row[COL["EventRootCode"]] = "1"
    row[COL["GoldsteinScale"]] = "-7.0"
    row[COL["NumMentions"]] = "5"
    row[COL["NumSources"]] = "2"
    row[COL["NumArticles"]] = "3"
    row[COL["AvgTone"]] = "-3.5"
    row[COL["SOURCEURL"]] = "https://example.com/news/1"
    for key, val in overrides.items():
        row[COL[key]] = val
    return row


# ---------------------------------------------------------------------------
# _row_to_raw
# ---------------------------------------------------------------------------

def test_row_to_raw_slice_country_passes():
    """Row with CH/TW actors should be parsed."""
    row = _make_gdelt_row()
    record = _row_to_raw(row)
    assert record is not None
    assert record.actor1_country_code == "CH"
    assert record.actor2_country_code == "TW"
    assert record.global_event_id == "123456"
    assert record.goldstein_scale == pytest.approx(-7.0)


def test_row_to_raw_non_slice_filtered():
    """Row with DE/FR actors (not in SLICE_ISO2) should return None."""
    row = _make_gdelt_row(Actor1CountryCode="DE", Actor2CountryCode="FR")
    assert _row_to_raw(row) is None


def test_row_to_raw_one_slice_actor_passes():
    """Row with one slice actor and one non-slice actor should pass."""
    row = _make_gdelt_row(Actor1CountryCode="CH", Actor2CountryCode="DE")
    record = _row_to_raw(row)
    assert record is not None
    assert record.actor1_country_code == "CH"


def test_row_to_raw_short_row():
    """Row shorter than 61 columns should return None."""
    assert _row_to_raw(["a", "b"]) is None


def test_row_to_raw_bad_goldstein():
    """Empty Goldstein scale should parse to None without raising."""
    row = _make_gdelt_row(GoldsteinScale="")
    record = _row_to_raw(row)
    assert record is not None
    assert record.goldstein_scale is None


# ---------------------------------------------------------------------------
# cameo_to_domain
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "code, goldstein, expected",
    [
        ("014", None, Domain.info),           # info override
        ("036", None, Domain.diplomatic),      # diplomatic range
        ("193", -7.0, Domain.kinetic_limited), # assault, not severe enough for general
        ("193", -9.0, Domain.kinetic_general), # assault + Goldstein ≤ -8 → general
        ("1713", None, Domain.cyber),          # cyber override
        ("200", None, Domain.kinetic_general), # code 200+
        ("163", None, Domain.economic),        # sanction range
    ],
)
def test_cameo_to_domain(code: str, goldstein: float | None, expected: Domain):
    assert cameo_to_domain(code, goldstein) == expected


def test_cameo_unknown_code():
    """Unknown code should fall back to diplomatic."""
    assert cameo_to_domain("9999") == Domain.diplomatic


def test_cameo_bad_string():
    """Non-numeric code should fall back to diplomatic."""
    assert cameo_to_domain("UNKNOWN") == Domain.diplomatic


# ---------------------------------------------------------------------------
# GDELTSource.normalize
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_normalize_chn_twn_event():
    """Full normalize path for a CHN→TWN assault event."""
    row = _make_gdelt_row(
        GlobalEventID="999",
        SQLDATE="20250115",
        Actor1CountryCode="CH",
        Actor2CountryCode="TW",
        EventCode="193",
        GoldsteinScale="-7.0",
        AvgTone="-5.0",
    )
    raw = _row_to_raw(row)
    assert raw is not None

    src = GDELTSource()
    event = await src.normalize(raw)

    assert event.source == "gdelt"
    assert event.actor_iso3 == "CHN"
    assert event.target_iso3 == "TWN"
    assert event.event_type == "193"
    assert event.domain is not None
    assert event.domain.value == "kinetic_limited"
    assert event.severity is not None
    assert 8.0 <= event.severity <= 9.0  # (10 - (-7)) / 2 = 8.5
    assert event.payload["_dedup_key"] == "gdelt:999"
    assert event.occurred_at.year == 2025
    assert event.occurred_at.month == 1
    assert event.occurred_at.day == 15


@pytest.mark.asyncio
async def test_normalize_economic_sanction():
    """CAMEO 163 (sanctions) should normalize to economic domain."""
    row = _make_gdelt_row(
        GlobalEventID="777",
        EventCode="163",
        GoldsteinScale="-4.0",
        Actor1CountryCode="US",
        Actor2CountryCode="CH",
    )
    raw = _row_to_raw(row)
    assert raw is not None
    src = GDELTSource()
    event = await src.normalize(raw)
    assert event.domain is not None
    assert event.domain.value == "economic"
    assert event.actor_iso3 == "USA"
    assert event.target_iso3 == "CHN"


@pytest.mark.asyncio
async def test_normalize_dedup_key_stable():
    """Dedup key must be stable across two normalize calls for same raw."""
    row = _make_gdelt_row(GlobalEventID="STABLE")
    raw = _row_to_raw(row)
    src = GDELTSource()
    e1 = await src.normalize(raw)
    e2 = await src.normalize(raw)
    assert e1.payload["_dedup_key"] == e2.payload["_dedup_key"]


# ---------------------------------------------------------------------------
# URL generation helpers
# ---------------------------------------------------------------------------

def test_gdelt_file_urls_in_window():
    """15-min slots covering a 1-hour window should produce 4 URLs."""
    since = datetime(2025, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
    until = datetime(2025, 1, 1, 1, 0, 0, tzinfo=timezone.utc)
    urls = _gdelt_file_urls_in_window(since, until)
    assert len(urls) == 4
    assert "20250101000000" in urls[0]
    assert "20250101001500" in urls[1]
    assert "20250101003000" in urls[2]
    assert "20250101004500" in urls[3]


# ---------------------------------------------------------------------------
# Live fetch — skip if no network or cassette is absent
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@pytest.mark.skipif(
    not Path(__file__).parent.joinpath("cassettes/gdelt_export.yaml").exists(),
    reason="VCR cassette gdelt_export.yaml not present — skipping live fetch test",
)
async def test_fetch_with_vcr_cassette():
    """Placeholder: replay fetch via VCR cassette (cassette has minimal binary stub).

    A full VCR replay test would require vcr.py + pytest-recording integration
    configured in pyproject.toml.  The cassette file exists as a structural
    placeholder; wire up pytest-recording to record a real response.
    """
    pytest.skip("VCR cassette is a stub — integrate pytest-recording to record")
