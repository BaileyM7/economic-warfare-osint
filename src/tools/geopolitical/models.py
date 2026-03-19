"""Pydantic models for geopolitical risk and conflict data."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class GdeltEvent(BaseModel):
    """A single event from the GDELT 2.0 API."""

    event_id: str = ""
    date: datetime | None = None
    actor1_name: str = ""
    actor1_country: str = ""
    actor2_name: str = ""
    actor2_country: str = ""
    event_code: str = ""
    goldstein_scale: float | None = None
    num_mentions: int = 0
    avg_tone: float | None = None
    source_url: str = ""


class AcledEvent(BaseModel):
    """A single event from the ACLED conflict data API."""

    event_id: str = ""
    event_date: datetime | None = None
    event_type: str = ""
    sub_event_type: str = ""
    actor1: str = ""
    actor2: str = ""
    country: str = ""
    location: str = ""
    fatalities: int = 0
    notes: str = ""
    source: str = ""


class ConflictSummary(BaseModel):
    """Aggregated conflict statistics for a country over a period."""

    country: str
    period: str = ""  # e.g. "2025-01-01 to 2025-03-19"
    total_events: int = 0
    total_fatalities: int = 0
    event_type_breakdown: dict[str, int] = Field(default_factory=dict)
    escalation_trend: str = ""  # "escalating", "stable", "de-escalating"


class GeopoliticalRiskProfile(BaseModel):
    """Combined risk profile for a country from multiple sources."""

    country: str
    conflict_intensity: str = ""  # "none", "low", "moderate", "high", "extreme"
    recent_events: list[GdeltEvent | AcledEvent] = Field(default_factory=list)
    risk_score: float = 0.0  # 0.0 (safe) to 10.0 (extreme risk)
    narrative_summary: str = ""


class BilateralTensionReport(BaseModel):
    """Events and tension analysis between two countries."""

    country1: str
    country2: str
    period: str = ""
    events: list[GdeltEvent] = Field(default_factory=list)
    avg_goldstein: float | None = None
    avg_tone: float | None = None
    event_count: int = 0
    tension_level: str = ""  # "cooperative", "neutral", "tense", "hostile"


class EventTimeline(BaseModel):
    """Timeline of event intensity over a period."""

    query: str
    period: str = ""
    data_points: list[dict[str, Any]] = Field(default_factory=list)
    total_events: int = 0
    peak_date: str = ""
    peak_count: int = 0
