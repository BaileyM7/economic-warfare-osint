"""Unit tests for :mod:`ai.sim.signals`."""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from wargame_ai.sim.signals import (
    Signal,
    SignalCollector,
    render_signals_block,
)


class _StubExtractor:
    """Extractor that returns a fixed Signal (or None) without touching DB."""

    def __init__(self, source: str, signal: Signal | None) -> None:
        self.source = source
        self._signal = signal

    async def extract(
        self,
        session: Any,
        iso3: str,
        *,
        window_hours: int = 24,
    ) -> Signal | None:
        return self._signal


class _BoomExtractor:
    """Always raises — collector must swallow it and continue."""

    source = "Boom"

    async def extract(self, session: Any, iso3: str, *, window_hours: int = 24) -> Signal | None:
        raise RuntimeError("synthetic extractor failure")


class TestSignal:
    def test_headline_length_capped(self) -> None:
        with pytest.raises(ValidationError):
            Signal(source="X", headline="a" * 121, magnitude=0.5)

    def test_magnitude_range(self) -> None:
        with pytest.raises(ValidationError):
            Signal(source="X", headline="ok", magnitude=1.5)
        with pytest.raises(ValidationError):
            Signal(source="X", headline="ok", magnitude=-0.1)


class TestSignalCollector:
    @pytest.mark.asyncio
    async def test_drops_below_floor_and_caps_max(self) -> None:
        signals = [
            Signal(source=f"S{i}", headline=f"hl{i}", magnitude=mag)
            for i, mag in enumerate([0.95, 0.10, 0.80, 0.50, 0.05, 0.90, 0.45, 0.20, 0.85, 0.60])
        ]
        extractors = [
            _StubExtractor(s.source, s) for s in signals
        ]
        collector = SignalCollector(
            extractors, magnitude_floor=0.30, max_signals=5
        )
        result = await collector.collect_for(session=None, iso3="CHN")  # type: ignore[arg-type]

        assert len(result) == 5
        # Sorted by magnitude desc
        mags = [s.magnitude for s in result]
        assert mags == sorted(mags, reverse=True)
        # All above floor
        assert min(mags) >= 0.30

    @pytest.mark.asyncio
    async def test_none_signals_omitted(self) -> None:
        extractors = [
            _StubExtractor("Real", Signal(source="Real", headline="ok", magnitude=0.7)),
            _StubExtractor("Quiet", None),
        ]
        collector = SignalCollector(extractors)
        result = await collector.collect_for(session=None, iso3="CHN")  # type: ignore[arg-type]
        assert [s.source for s in result] == ["Real"]

    @pytest.mark.asyncio
    async def test_one_failing_extractor_does_not_poison_others(self) -> None:
        extractors = [
            _BoomExtractor(),
            _StubExtractor("OK", Signal(source="OK", headline="ok", magnitude=0.7)),
        ]
        collector = SignalCollector(extractors)
        result = await collector.collect_for(session=None, iso3="CHN")  # type: ignore[arg-type]
        assert [s.source for s in result] == ["OK"]

    @pytest.mark.asyncio
    async def test_empty_extractors_returns_empty_list(self) -> None:
        collector = SignalCollector([])
        result = await collector.collect_for(session=None, iso3="CHN")  # type: ignore[arg-type]
        assert result == []


class TestRenderSignalsBlock:
    def test_empty_returns_no_intelligence_line(self) -> None:
        assert "no material intelligence" in render_signals_block([])

    def test_renders_bullets_with_direction_arrows(self) -> None:
        signals = [
            Signal(source="GDELT", headline="tone -7.8", magnitude=0.62, direction="negative"),
            Signal(source="FRED", headline="DGS10 +0.30", magnitude=0.40, direction="neutral"),
        ]
        block = render_signals_block(signals)
        assert "**[GDELT]**" in block
        assert "↓" in block  # negative arrow
        assert "·" in block  # neutral arrow
        assert "tone -7.8" in block
        assert "(mag 0.62)" in block
