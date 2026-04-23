"""Tests for the ACLED adapter — normalize path and event-type mapping.

VCR cassette: tests/ingest/cassettes/acled_read.yaml
Contains two fixture events:
  - Strategic developments (Taiwan Strait transit) → Domain.info
  - Battles / Armed clash (Kinmen, 2 fatalities) → Domain.kinetic_limited

Real HTTP is never hit; tests that call fetch() skip if ACLED credentials
are absent in the environment.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from wargame_backend.app.db.models import EventDomain
from ingest.acled import (
    ACLEDRawRecord,
    ACLEDSource,
    _event_type_to_domain,
    _fatalities_to_severity,
)


# ---------------------------------------------------------------------------
# _event_type_to_domain
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "event_type, expected",
    [
        ("Battles", EventDomain.kinetic_limited),
        ("Violence against civilians", EventDomain.kinetic_limited),
        ("Explosions/Remote violence", EventDomain.kinetic_limited),
        ("Riots", EventDomain.kinetic_limited),
        ("Protests", EventDomain.diplomatic),
        ("Strategic developments", EventDomain.info),
        ("Unknown type", EventDomain.kinetic_limited),  # fallback
    ],
)
def test_event_type_to_domain(event_type: str, expected: EventDomain):
    assert _event_type_to_domain(event_type) == expected


# ---------------------------------------------------------------------------
# _fatalities_to_severity
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "fatalities, expected_range",
    [
        (0, (0.9, 1.1)),
        (1, (2.0, 2.5)),
        (9, (5.9, 6.1)),
        (10, (6.0, 6.5)),
        (100, (9.9, 10.1)),
        (1000, (9.9, 10.1)),  # capped at 10
    ],
)
def test_fatalities_to_severity(fatalities: int, expected_range: tuple[float, float]):
    sev = _fatalities_to_severity(fatalities)
    lo, hi = expected_range
    assert lo <= sev <= hi, f"fatalities={fatalities} gave severity={sev}"


# ---------------------------------------------------------------------------
# ACLEDSource.normalize
# ---------------------------------------------------------------------------

def _make_acled_raw(**overrides) -> ACLEDRawRecord:
    defaults = dict(
        data_id="12345",
        event_id_cnty="TWN2025000001",
        event_date="2025-01-02",
        year="2025",
        time_precision=1,
        event_type="Strategic developments",
        sub_event_type="Looting/property destruction",
        actor1="Military Forces of China",
        assoc_actor_1="",
        inter1="1",
        actor2="",
        assoc_actor_2="",
        inter2="0",
        interaction="10",
        civilian_targeting="",
        iso="158",
        region="East Asia",
        country="Taiwan",
        admin1="",
        admin2="",
        admin3="",
        location="Taiwan Strait",
        latitude="23.5",
        longitude="121.0",
        geo_precision=3,
        source="Reuters",
        source_scale="International",
        notes="Chinese military vessel transited Taiwan Strait.",
        fatalities=0,
        tags="",
        timestamp="1735776000",
    )
    defaults.update(overrides)
    return ACLEDRawRecord(**defaults)


@pytest.mark.asyncio
async def test_normalize_strategic_development():
    """Strategic development event → info domain, actor=TWN (iso 158)."""
    raw = _make_acled_raw()
    src = ACLEDSource()
    event = await src.normalize(raw)

    assert event.source == "acled"
    assert event.domain == EventDomain.info
    assert event.actor_iso3 == "TWN"  # iso=158 → TWN
    assert event.occurred_at.year == 2025
    assert event.occurred_at.month == 1
    assert event.occurred_at.day == 2
    assert event.payload["_dedup_key"] == "acled:12345"
    assert event.payload["fatalities"] == 0
    assert event.severity is not None
    assert event.severity == pytest.approx(1.0)


@pytest.mark.asyncio
async def test_normalize_battle_with_fatalities():
    """Battle with fatalities → kinetic_limited, elevated severity."""
    raw = _make_acled_raw(
        data_id="99999",
        event_type="Battles",
        sub_event_type="Armed clash",
        actor1="Military Forces of China",
        actor2="Military Forces of Taiwan",
        iso="158",
        country="Taiwan",
        fatalities=2,
        notes="Shots fired near Kinmen island.",
    )
    src = ACLEDSource()
    event = await src.normalize(raw)

    assert event.domain == EventDomain.kinetic_limited
    assert event.severity is not None
    assert event.severity > 1.0  # higher than zero-fatality event
    assert event.payload["_dedup_key"] == "acled:99999"


@pytest.mark.asyncio
async def test_normalize_protest():
    """Protest event → diplomatic domain."""
    raw = _make_acled_raw(
        event_type="Protests",
        sub_event_type="Peaceful protest",
        iso="356",
        country="India",
    )
    src = ACLEDSource()
    event = await src.normalize(raw)
    assert event.domain == EventDomain.diplomatic
    assert event.actor_iso3 == "IND"


@pytest.mark.asyncio
async def test_normalize_dedup_key_stable():
    """Dedup key must be deterministic across calls."""
    raw = _make_acled_raw(data_id="STABLE123")
    src = ACLEDSource()
    e1 = await src.normalize(raw)
    e2 = await src.normalize(raw)
    assert e1.payload["_dedup_key"] == e2.payload["_dedup_key"] == "acled:STABLE123"


@pytest.mark.asyncio
async def test_normalize_bad_date_fallback():
    """Malformed event_date should not raise; falls back to now."""
    raw = _make_acled_raw(event_date="not-a-date")
    src = ACLEDSource()
    event = await src.normalize(raw)
    assert event.occurred_at is not None


# ---------------------------------------------------------------------------
# fetch — skip if no ACLED credentials
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_fetch_skips_without_credentials():
    """fetch() should raise RuntimeError when ACLED credentials are absent."""
    src = ACLEDSource()
    # Temporarily clear credentials
    with patch.dict(os.environ, {"ACLED_API_KEY": "", "ACLED_EMAIL": ""}, clear=False):
        with pytest.raises(RuntimeError, match="ACLED_API_KEY"):
            async for _ in src.fetch(
                datetime(2025, 1, 1, tzinfo=timezone.utc),
                datetime(2025, 1, 7, tzinfo=timezone.utc),
            ):
                pass


@pytest.mark.asyncio
async def test_fetch_uses_vcr_cassette():
    """Replay ACLED API response from cassette (skips if cassette absent)."""
    cassette = (
        Path(__file__).parent / "cassettes" / "acled_read.yaml"
    )
    if not cassette.exists():
        pytest.skip("VCR cassette acled_read.yaml not present")

    if not os.environ.get("ACLED_API_KEY") or not os.environ.get("ACLED_EMAIL"):
        pytest.skip(
            "ACLED_API_KEY / ACLED_EMAIL not set — cannot replay cassette "
            "without credentials in request params"
        )

    # Cassette replay would be wired via pytest-recording / vcrpy decorator.
    # This test is a structural placeholder for that integration.
    pytest.skip("Integrate pytest-recording to replay cassette automatically")
