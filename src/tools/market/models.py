"""Pydantic models for the Market Data MCP tool."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, Field


class StockProfile(BaseModel):
    """Core company profile information."""
    ticker: str
    name: str
    market_cap: float | None = None
    sector: str | None = None
    industry: str | None = None
    country: str | None = None
    exchange: str | None = None
    description: str | None = None


class HistoricalPrice(BaseModel):
    """A single day's closing price."""
    date: str  # ISO date string
    close: float


class PriceData(BaseModel):
    """Current and historical price information for a ticker."""
    ticker: str
    current_price: float | None = None
    change_pct: float | None = None
    volume: int | None = None
    fifty_two_week_high: float | None = None
    fifty_two_week_low: float | None = None
    historical: list[HistoricalPrice] = Field(default_factory=list)


class InstitutionalHolder(BaseModel):
    """A single institutional holder of a stock."""
    holder_name: str
    shares: int | None = None
    value: float | None = None
    pct_held: float | None = None
    date_reported: str | None = None


class AnalystEstimate(BaseModel):
    """Analyst consensus data for a stock."""
    target_price: float | None = None
    recommendation: str | None = None
    num_analysts: int | None = None


class ExposureReport(BaseModel):
    """US/allied institutional exposure analysis for a target entity."""
    entity_name: str
    ticker: str | None = None
    us_institutional_holders: list[InstitutionalHolder] = Field(default_factory=list)
    total_us_exposure_usd: float | None = None
    pension_fund_exposure: list[InstitutionalHolder] = Field(default_factory=list)
    analyst_estimate: AnalystEstimate | None = None


class MarketEntityResult(BaseModel):
    """Result from searching for a market entity."""
    name: str
    ticker: str | None = None
    cik: str | None = None
    source: str  # "yfinance", "sec_edgar"
    exchange: str | None = None


class MacroObservation(BaseModel):
    """A single observation from a FRED time series."""
    date: str
    value: float | None = None


class MacroSeries(BaseModel):
    """FRED macroeconomic time series data."""
    series_id: str
    title: str | None = None
    units: str | None = None
    frequency: str | None = None
    observations: list[MacroObservation] = Field(default_factory=list)
