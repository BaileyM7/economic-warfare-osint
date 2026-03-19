"""Pydantic models for the Economic Modeling MCP tool."""

from __future__ import annotations

from datetime import date

from pydantic import BaseModel, Field


class EconomicIndicator(BaseModel):
    """A single macroeconomic indicator observation."""

    indicator_id: str
    name: str
    country: str
    value: float | None = None
    unit: str = ""
    date: str = ""  # ISO date or year string
    source: str = ""  # "FRED", "IMF", "World Bank"


class CountryEconomicProfile(BaseModel):
    """Snapshot of a country's key economic metrics."""

    country: str
    gdp_usd: float | None = None  # nominal GDP in USD
    gdp_growth_pct: float | None = None
    inflation_pct: float | None = None
    unemployment_pct: float | None = None
    reserves_usd: float | None = None  # foreign reserves in USD
    debt_to_gdp_pct: float | None = None
    fdi_inflows_usd: float | None = None  # foreign direct investment inflows
    top_sectors: list[str] = Field(default_factory=list)


class CommodityPrice(BaseModel):
    """Price point for a globally traded commodity."""

    commodity: str
    price: float | None = None
    unit: str = ""  # e.g. "USD/barrel", "USD/troy oz"
    currency: str = "USD"
    date: str = ""  # ISO date string
    change_pct: float | None = None  # period-over-period change


class SanctionImpactEstimate(BaseModel):
    """Heuristic estimate of sanctions impact on a target economy."""

    target_country: str
    gdp_impact_pct: float | None = None  # estimated GDP contraction percentage
    trade_impact_usd: float | None = None  # estimated trade volume loss in USD
    sectors_affected: list[str] = Field(default_factory=list)
    confidence: str = "LOW"  # HIGH / MEDIUM / LOW
