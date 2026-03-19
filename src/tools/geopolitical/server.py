"""MCP server for geopolitical risk and conflict intelligence.

Data sources:
- GDELT 2.0 (no key required)
- ACLED (requires free API key + email; gracefully degrades without it)
"""

from __future__ import annotations

import logging
from collections import Counter
from datetime import datetime

from mcp.server.fastmcp import FastMCP

from ...common.types import Confidence, SourceReference, ToolResponse
from .client import (
    acled_get_events,
    gdelt_bilateral_search,
    gdelt_doc_search,
    gdelt_timeline,
    is_acled_available,
)
from .models import (
    BilateralTensionReport,
    ConflictSummary,
    EventTimeline,
    GeopoliticalRiskProfile,
)

logger = logging.getLogger(__name__)

mcp = FastMCP("geopolitical")

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_GDELT_SOURCE = SourceReference(
    name="GDELT 2.0",
    url="https://api.gdeltproject.org/api/v2/",
)

_ACLED_SOURCE = SourceReference(
    name="ACLED",
    url="https://acleddata.com",
)


def _sources_used(include_acled: bool = False) -> list[SourceReference]:
    """Build the sources list depending on which APIs were actually queried."""
    srcs = [
        SourceReference(
            name=_GDELT_SOURCE.name,
            url=_GDELT_SOURCE.url,
            accessed_at=datetime.utcnow(),
        )
    ]
    if include_acled:
        srcs.append(
            SourceReference(
                name=_ACLED_SOURCE.name,
                url=_ACLED_SOURCE.url,
                accessed_at=datetime.utcnow(),
            )
        )
    return srcs


def _classify_conflict_intensity(risk_score: float) -> str:
    """Map a 0-10 risk score to a human-readable intensity label."""
    if risk_score <= 1.0:
        return "none"
    if risk_score <= 3.0:
        return "low"
    if risk_score <= 5.0:
        return "moderate"
    if risk_score <= 7.5:
        return "high"
    return "extreme"


def _classify_tension_level(avg_goldstein: float | None) -> str:
    """Map average Goldstein scale to a tension label.

    Goldstein scale ranges roughly from -10 (most conflictual) to +10 (most
    cooperative).  GDELT tone is used as proxy when Goldstein is unavailable.
    """
    if avg_goldstein is None:
        return "unknown"
    if avg_goldstein >= 3.0:
        return "cooperative"
    if avg_goldstein >= -1.0:
        return "neutral"
    if avg_goldstein >= -5.0:
        return "tense"
    return "hostile"


def _determine_escalation(events_by_half: tuple[int, int]) -> str:
    """Determine escalation trend by comparing first-half vs second-half counts."""
    first, second = events_by_half
    if first == 0 and second == 0:
        return "no data"
    if second > first * 1.25:
        return "escalating"
    if second < first * 0.75:
        return "de-escalating"
    return "stable"


def _compute_risk_score(
    gdelt_count: int,
    acled_count: int,
    total_fatalities: int,
    avg_tone: float | None,
) -> float:
    """Compute a 0-10 risk score from available indicators."""
    score = 0.0

    # Event volume component (0-3 points)
    total_events = gdelt_count + acled_count
    if total_events > 200:
        score += 3.0
    elif total_events > 100:
        score += 2.0
    elif total_events > 30:
        score += 1.0
    elif total_events > 5:
        score += 0.5

    # Fatalities component (0-4 points)
    if total_fatalities > 500:
        score += 4.0
    elif total_fatalities > 100:
        score += 3.0
    elif total_fatalities > 20:
        score += 2.0
    elif total_fatalities > 0:
        score += 1.0

    # Tone / sentiment component (0-3 points)
    if avg_tone is not None:
        if avg_tone < -5.0:
            score += 3.0
        elif avg_tone < -3.0:
            score += 2.0
        elif avg_tone < -1.0:
            score += 1.0

    return min(round(score, 1), 10.0)


def _confidence_for_data(
    gdelt_count: int, acled_available: bool, acled_count: int
) -> Confidence:
    """Determine confidence based on data completeness."""
    if acled_available and acled_count > 0 and gdelt_count > 0:
        return Confidence.HIGH
    if gdelt_count > 10:
        return Confidence.MEDIUM
    return Confidence.LOW


# ---------------------------------------------------------------------------
# MCP Tools
# ---------------------------------------------------------------------------


@mcp.tool()
async def search_events(query: str, days: int = 30) -> dict:
    """Search GDELT for recent geopolitical events matching a query.

    Args:
        query: Free-text search (country names, topics, entities).
        days: How many days back to search (default 30).

    Returns:
        ToolResponse with list of matching events and metadata.
    """
    events = await gdelt_doc_search(query, days=days)

    response = ToolResponse(
        data={
            "query": query,
            "days": days,
            "event_count": len(events),
            "events": [e.model_dump(mode="json") for e in events],
        },
        confidence=Confidence.MEDIUM if events else Confidence.LOW,
        sources=_sources_used(include_acled=False),
    )
    return response.model_dump(mode="json")


@mcp.tool()
async def get_conflict_data(country: str, days: int = 90) -> dict:
    """Get conflict event data for a country from ACLED (with GDELT fallback).

    Args:
        country: Country name (e.g. "Ukraine", "Ethiopia").
        days: How many days back to query (default 90).

    Returns:
        ToolResponse with ConflictSummary including event breakdown and
        escalation trend.
    """
    errors: list[str] = []
    acled_events = await acled_get_events(country, days=days)
    used_acled = is_acled_available() and len(acled_events) > 0

    if not used_acled:
        if not is_acled_available():
            errors.append(
                "ACLED credentials not configured; using GDELT-only fallback"
            )
        # Fallback: use GDELT doc search for the country
        gdelt_events = await gdelt_doc_search(f'"{country}" conflict', days=days)
    else:
        gdelt_events = []

    # Build summary
    if used_acled:
        total_fatalities = sum(e.fatalities for e in acled_events)
        type_breakdown = dict(Counter(e.event_type for e in acled_events))
        total_events = len(acled_events)

        # Escalation: split events into first/second half of period
        midpoint = len(acled_events) // 2
        first_half_count = midpoint
        second_half_count = total_events - midpoint
    else:
        total_fatalities = 0
        type_breakdown = {"gdelt_articles": len(gdelt_events)}
        total_events = len(gdelt_events)
        midpoint = total_events // 2
        first_half_count = midpoint
        second_half_count = total_events - midpoint

    from datetime import timedelta

    end = datetime.utcnow()
    start = end - timedelta(days=days)
    period = f"{start.strftime('%Y-%m-%d')} to {end.strftime('%Y-%m-%d')}"

    summary = ConflictSummary(
        country=country,
        period=period,
        total_events=total_events,
        total_fatalities=total_fatalities,
        event_type_breakdown=type_breakdown,
        escalation_trend=_determine_escalation((first_half_count, second_half_count)),
    )

    confidence = Confidence.HIGH if used_acled else Confidence.MEDIUM
    if total_events == 0:
        confidence = Confidence.LOW

    response = ToolResponse(
        data=summary.model_dump(mode="json"),
        confidence=confidence,
        sources=_sources_used(include_acled=used_acled),
        errors=errors,
    )
    return response.model_dump(mode="json")


@mcp.tool()
async def get_risk_profile(country: str) -> dict:
    """Build a combined geopolitical risk profile for a country.

    Merges GDELT media analysis with ACLED conflict data to produce a
    composite risk score (0-10) and narrative summary.

    Args:
        country: Country name (e.g. "Iran", "Myanmar").

    Returns:
        ToolResponse with GeopoliticalRiskProfile.
    """
    errors: list[str] = []

    # Fetch data from both sources in parallel-safe manner
    gdelt_events = await gdelt_doc_search(f'"{country}"', days=90)
    acled_events = await acled_get_events(country, days=90)

    used_acled = is_acled_available() and len(acled_events) > 0
    if not is_acled_available():
        errors.append("ACLED credentials not configured; risk profile uses GDELT only")

    # Compute indicators
    total_fatalities = sum(e.fatalities for e in acled_events)
    tones = [e.avg_tone for e in gdelt_events if e.avg_tone is not None]
    avg_tone = sum(tones) / len(tones) if tones else None

    risk_score = _compute_risk_score(
        gdelt_count=len(gdelt_events),
        acled_count=len(acled_events),
        total_fatalities=total_fatalities,
        avg_tone=avg_tone,
    )

    # Build narrative
    parts: list[str] = []
    parts.append(f"Risk assessment for {country} based on last 90 days of data.")
    parts.append(f"GDELT found {len(gdelt_events)} media articles.")
    if used_acled:
        parts.append(
            f"ACLED reports {len(acled_events)} conflict events with "
            f"{total_fatalities} fatalities."
        )
    if avg_tone is not None:
        direction = "negative" if avg_tone < 0 else "positive"
        parts.append(f"Average media tone is {direction} ({avg_tone:.2f}).")
    parts.append(
        f"Overall risk score: {risk_score}/10 "
        f"({_classify_conflict_intensity(risk_score)})."
    )

    # Combine recent events (most recent first, limited to 20)
    recent_events: list[dict] = []
    for ev in sorted(
        gdelt_events, key=lambda e: e.date or datetime.min, reverse=True
    )[:10]:
        recent_events.append(ev.model_dump(mode="json"))
    for ev in sorted(
        acled_events, key=lambda e: e.event_date or datetime.min, reverse=True
    )[:10]:
        recent_events.append(ev.model_dump(mode="json"))

    profile = GeopoliticalRiskProfile(
        country=country,
        conflict_intensity=_classify_conflict_intensity(risk_score),
        recent_events=[],  # stored separately in data dict for serialization
        risk_score=risk_score,
        narrative_summary=" ".join(parts),
    )

    confidence = _confidence_for_data(
        len(gdelt_events), is_acled_available(), len(acled_events)
    )

    response = ToolResponse(
        data={
            **profile.model_dump(mode="json"),
            "recent_events": recent_events,
        },
        confidence=confidence,
        sources=_sources_used(include_acled=used_acled),
        errors=errors,
    )
    return response.model_dump(mode="json")


@mcp.tool()
async def get_bilateral_tensions(
    country1: str, country2: str, days: int = 90
) -> dict:
    """Analyze tensions between two countries using GDELT event data.

    Args:
        country1: First country name.
        country2: Second country name.
        days: How many days back to analyze (default 90).

    Returns:
        ToolResponse with BilateralTensionReport.
    """
    events = await gdelt_bilateral_search(country1, country2, days=days)

    tones = [e.avg_tone for e in events if e.avg_tone is not None]
    avg_tone = sum(tones) / len(tones) if tones else None
    goldsteins = [e.goldstein_scale for e in events if e.goldstein_scale is not None]
    avg_goldstein = sum(goldsteins) / len(goldsteins) if goldsteins else None

    # Use avg_tone as Goldstein proxy if actual Goldstein not available
    tension_indicator = avg_goldstein if avg_goldstein is not None else avg_tone

    from datetime import timedelta

    end = datetime.utcnow()
    start = end - timedelta(days=days)
    period = f"{start.strftime('%Y-%m-%d')} to {end.strftime('%Y-%m-%d')}"

    report = BilateralTensionReport(
        country1=country1,
        country2=country2,
        period=period,
        events=events[:50],  # cap serialized events
        avg_goldstein=avg_goldstein,
        avg_tone=avg_tone,
        event_count=len(events),
        tension_level=_classify_tension_level(tension_indicator),
    )

    confidence = Confidence.MEDIUM if events else Confidence.LOW

    response = ToolResponse(
        data=report.model_dump(mode="json"),
        confidence=confidence,
        sources=_sources_used(include_acled=False),
    )
    return response.model_dump(mode="json")


@mcp.tool()
async def get_event_timeline(query: str, days: int = 180) -> dict:
    """Get a timeline of event intensity from GDELT for a query.

    Args:
        query: Free-text search (e.g. "China Taiwan military").
        days: How many days back to chart (default 180).

    Returns:
        ToolResponse with EventTimeline showing volume over time.
    """
    data_points = await gdelt_timeline(query, days=days)

    total = sum(p.get("count", 0) for p in data_points)
    peak_point = max(data_points, key=lambda p: p.get("count", 0)) if data_points else {}
    peak_date = peak_point.get("date", "")
    peak_count = peak_point.get("count", 0)

    from datetime import timedelta

    end = datetime.utcnow()
    start = end - timedelta(days=days)
    period = f"{start.strftime('%Y-%m-%d')} to {end.strftime('%Y-%m-%d')}"

    timeline = EventTimeline(
        query=query,
        period=period,
        data_points=data_points,
        total_events=total,
        peak_date=str(peak_date),
        peak_count=int(peak_count),
    )

    confidence = Confidence.MEDIUM if data_points else Confidence.LOW

    response = ToolResponse(
        data=timeline.model_dump(mode="json"),
        confidence=confidence,
        sources=_sources_used(include_acled=False),
    )
    return response.model_dump(mode="json")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    mcp.run()
