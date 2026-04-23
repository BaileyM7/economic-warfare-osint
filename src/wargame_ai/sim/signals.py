"""Signal extraction layer between the data lake and the agent prompt.

Each ingest adapter (GDELT, ACLED, World Bank, …) writes raw rows into the
``events`` table.  Pumping that raw stream into every country-agent's system
prompt would explode the context window — a 10-country, 20-turn sim would
re-send hundreds of rows per turn.

This module defines the compression layer:

* ``Signal`` — a fixed-size, one-line briefing (≤120 char headline + magnitude).
* ``SignalExtractor`` — protocol every adapter implements; given an ISO3 +
  time window, return at most one ``Signal`` summarizing what the country
  should know about this source this turn.  ``None`` means "nothing
  material" — silence is the default.
* ``SignalCollector`` — fan-out over registered extractors, threshold,
  rank by magnitude, cap at N, and render a markdown block for the prompt.

The collector is intentionally synchronous-async neutral: it accepts an
async ``AsyncSession`` so extractors can hit Postgres, and runs them in
parallel so adding sources stays cheap.
"""

from __future__ import annotations

import asyncio
from typing import Literal, Protocol

import structlog
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

log = structlog.get_logger(__name__)


SignalDirection = Literal["positive", "negative", "neutral"]


class Signal(BaseModel):
    """One-line intelligence briefing surfaced to a country agent.

    Hard size limit on ``headline`` (120 chars) keeps the prompt-block size
    bounded regardless of how many extractors register.
    """

    model_config = ConfigDict(str_strip_whitespace=True)

    source: str = Field(min_length=1, max_length=32)
    headline: str = Field(min_length=1, max_length=120)
    magnitude: float = Field(ge=0.0, le=1.0)
    direction: SignalDirection = "neutral"
    # FK-ish reference to underlying evidence (event UUID, observation key,
    # etc.) — optional so extractors that summarize aggregates don't need to
    # invent a single canonical row.
    evidence_id: str = ""


class SignalExtractor(Protocol):
    """One extractor per registered data source.

    Implementations should be cheap: a single bounded SQL query over the
    ``events`` table (or equivalent), shaped to compute "did anything
    material change for ``iso3`` in the last ``window_hours``?"  Return
    ``None`` when the answer is no — that is the elegance lever.
    """

    source: str  # stable identifier, e.g. "GDELT" — surfaces in the prompt

    async def extract(
        self,
        session: AsyncSession,
        iso3: str,
        *,
        window_hours: int = 24,
    ) -> Signal | None:  # pragma: no cover — protocol only
        ...


class SignalCollector:
    """Fan-out signal extraction with thresholding and capping.

    Caller supplies a list of extractors at construction time (typically the
    ones registered in :mod:`ai.sim.extractors`).  ``collect_for`` calls
    every extractor in parallel, drops any signal below ``magnitude_floor``,
    sorts by magnitude descending, and truncates to ``max_signals``.
    """

    def __init__(
        self,
        extractors: list[SignalExtractor],
        *,
        magnitude_floor: float = 0.30,
        max_signals: int = 8,
        window_hours: int = 24,
    ) -> None:
        self._extractors = extractors
        self._magnitude_floor = magnitude_floor
        self._max_signals = max_signals
        self._window_hours = window_hours

    async def collect_for(
        self, session: AsyncSession, iso3: str
    ) -> list[Signal]:
        """Run every extractor in parallel, filter, rank, cap."""
        if not self._extractors:
            return []

        async def _safe_extract(ex: SignalExtractor) -> Signal | None:
            try:
                return await ex.extract(
                    session, iso3, window_hours=self._window_hours
                )
            except Exception as exc:  # noqa: BLE001
                # One bad extractor must not poison the whole turn.  Log and
                # skip — the agent gets a smaller intel block this turn.
                log.warning(
                    "signal_extractor_failed",
                    source=getattr(ex, "source", "unknown"),
                    iso3=iso3,
                    error=str(exc),
                )
                return None

        results = await asyncio.gather(
            *(_safe_extract(ex) for ex in self._extractors)
        )

        signals = [
            s for s in results
            if s is not None and s.magnitude >= self._magnitude_floor
        ]
        signals.sort(key=lambda s: s.magnitude, reverse=True)
        return signals[: self._max_signals]


def render_signals_block(signals: list[Signal]) -> str:
    """Render a list of Signals as a markdown bullet block for the prompt.

    Empty list → a single line acknowledging the vacuum, so the prompt slot
    is never visually missing (which would otherwise read as "the system
    forgot to include this").
    """
    if not signals:
        return "(no material intelligence above threshold this turn)"

    lines = []
    for s in signals:
        arrow = {"positive": "↑", "negative": "↓", "neutral": "·"}[s.direction]
        lines.append(
            f"- **[{s.source}]** {arrow} {s.headline}  _(mag {s.magnitude:.2f})_"
        )
    return "\n".join(lines)
