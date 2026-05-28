"""Tests for AISStreamExtractor — produces a Signal from buffered Events.

The extractor itself is exercised in the wargame simulation loop, where it
reads Events emitted by AISStreamSource and surfaces the largest week-over-week
delta in zone-presence ping counts. These tests cover the per-row decision
logic with mocked session.execute results to sidestep the conftest's
JSONB-on-SQLite incompatibility.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from wargame_ai.sim.extractors.aisstream import AISStreamExtractor


def _evt(
    pct: float | None, *, flag: str = "CHN", zone: str = "TWN_strait_buffer", id: str = "evt-1"
):
    return SimpleNamespace(
        id=id,
        payload={"ping_count_w_w_pct": pct, "flag_iso3": flag, "zone": zone},
    )


def _mock_session(events: list) -> AsyncMock:
    session = AsyncMock()
    result = MagicMock()
    result.scalars.return_value.all.return_value = events
    session.execute.return_value = result
    return session


@pytest.mark.asyncio
async def test_extractor_picks_largest_magnitude_pct():
    session = _mock_session(
        [
            _evt(pct=30.0, id="e1"),
            _evt(pct=150.0, id="e2"),  # largest
            _evt(pct=-40.0, id="e3"),
        ]
    )
    signal = await AISStreamExtractor().extract(session, "CHN")
    assert signal is not None
    assert signal.evidence_id == "e2"
    assert "+150% w/w" in signal.headline
    assert signal.source == "AISStream"
    assert "AIS position pings" in signal.headline  # the phrasing tweak we made


@pytest.mark.asyncio
async def test_extractor_returns_none_below_threshold():
    """Movement under ±25% is below the noise floor — no signal."""
    session = _mock_session([_evt(pct=10.0), _evt(pct=-15.0)])
    signal = await AISStreamExtractor().extract(session, "CHN")
    assert signal is None


@pytest.mark.asyncio
async def test_extractor_returns_none_when_no_events():
    session = _mock_session([])
    signal = await AISStreamExtractor().extract(session, "CHN")
    assert signal is None


@pytest.mark.asyncio
async def test_extractor_ignores_rows_missing_pct():
    """Rows without a numeric ping_count_w_w_pct are skipped."""
    session = _mock_session(
        [
            _evt(pct=None, id="e_nopct"),
            _evt(pct=80.0, id="e_signal"),
        ]
    )
    signal = await AISStreamExtractor().extract(session, "CHN")
    assert signal is not None
    assert signal.evidence_id == "e_signal"


@pytest.mark.asyncio
async def test_extractor_direction_inverts_with_sign():
    """Positive pct (more pings → bad for the target) flips to 'negative'."""
    positive_pct = await AISStreamExtractor().extract(_mock_session([_evt(pct=100.0)]), "CHN")
    negative_pct = await AISStreamExtractor().extract(_mock_session([_evt(pct=-100.0)]), "CHN")
    assert positive_pct.direction == "negative"
    assert negative_pct.direction == "positive"


@pytest.mark.asyncio
async def test_extractor_magnitude_caps_at_one():
    signal = await AISStreamExtractor().extract(_mock_session([_evt(pct=500.0)]), "CHN")
    assert signal.magnitude == 1.0


@pytest.mark.asyncio
async def test_extractor_headline_includes_flag_and_zone():
    signal = await AISStreamExtractor().extract(
        _mock_session([_evt(pct=80.0, flag="TWN", zone="SCS_spratlys")]),
        "TWN",
    )
    assert "TWN-flagged" in signal.headline
    assert "SCS_spratlys" in signal.headline
