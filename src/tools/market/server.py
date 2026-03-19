"""MCP server for Market Data tools (yfinance, SEC EDGAR, FRED)."""

from __future__ import annotations

import logging

from mcp.server.fastmcp import FastMCP

from ...common.types import Confidence, SourceReference, ToolResponse
from .client import (
    FREDClient,
    SECEdgarClient,
    YFinanceClient,
    build_exposure_report,
)

logger = logging.getLogger(__name__)

mcp = FastMCP("market-data")

# Shared client instances
_yf = YFinanceClient()
_sec = SECEdgarClient()
_fred = FREDClient()


@mcp.tool()
async def get_stock_profile(ticker: str) -> dict:
    """Get a company profile and current price for a stock ticker.

    Returns company name, sector, industry, country, market cap,
    exchange, a business description, and the current price snapshot.

    Args:
        ticker: Stock ticker symbol (e.g. "AAPL", "TSM", "BABA").
    """
    errors: list[str] = []
    profile = None
    price = None
    confidence = Confidence.HIGH

    try:
        profile = await _yf.get_stock_profile(ticker)
    except Exception as exc:
        errors.append(f"Profile fetch failed: {exc}")
        confidence = Confidence.LOW

    try:
        price = await _yf.get_price_data(ticker, period="5d")
        # Strip history for the profile call — just current snapshot
        if price:
            price.historical = []
    except Exception as exc:
        errors.append(f"Price fetch failed: {exc}")
        if confidence == Confidence.HIGH:
            confidence = Confidence.MEDIUM

    data = {
        "profile": profile.model_dump() if profile else None,
        "price": price.model_dump() if price else None,
    }

    return ToolResponse(
        data=data,
        confidence=confidence,
        sources=[SourceReference(name="yfinance", url=f"https://finance.yahoo.com/quote/{ticker}")],
        errors=errors,
    ).model_dump()


@mcp.tool()
async def get_price_history(ticker: str, period: str = "1y") -> dict:
    """Get historical price data for a stock ticker.

    Args:
        ticker: Stock ticker symbol (e.g. "AAPL").
        period: Lookback period — e.g. "1y", "6m", "5y", "max".
    """
    errors: list[str] = []
    try:
        price = await _yf.get_price_data(ticker, period=period)
        data = price.model_dump()
        confidence = Confidence.HIGH if price.historical else Confidence.LOW
    except Exception as exc:
        data = None
        errors.append(str(exc))
        confidence = Confidence.LOW

    return ToolResponse(
        data=data,
        confidence=confidence,
        sources=[SourceReference(name="yfinance", url=f"https://finance.yahoo.com/quote/{ticker}")],
        errors=errors,
    ).model_dump()


@mcp.tool()
async def get_institutional_holders(ticker: str) -> dict:
    """Get institutional holders of a stock, highlighting pension and sovereign funds.

    Useful for "friendly fire" analysis — identifying US/allied capital
    that would be affected if this entity is sanctioned or disrupted.

    Args:
        ticker: Stock ticker symbol.
    """
    errors: list[str] = []
    try:
        holders = await _yf.get_institutional_holders(ticker)
        data = [h.model_dump() for h in holders]
        confidence = Confidence.HIGH if holders else Confidence.LOW
    except Exception as exc:
        data = []
        errors.append(str(exc))
        confidence = Confidence.LOW

    return ToolResponse(
        data=data,
        confidence=confidence,
        sources=[SourceReference(name="yfinance", url=f"https://finance.yahoo.com/quote/{ticker}/holders")],
        errors=errors,
    ).model_dump()


@mcp.tool()
async def get_market_exposure(entity_name: str) -> dict:
    """Assess US/allied institutional exposure to a company ("friendly fire" check).

    Searches for the entity, then compiles a report of institutional holders,
    pension fund exposure, and analyst estimates.

    Args:
        entity_name: Company name or ticker to analyse (e.g. "Huawei", "SMIC", "TSM").
    """
    errors: list[str] = []
    confidence = Confidence.MEDIUM

    # Try the entity_name directly as a ticker first
    ticker = entity_name.upper()
    try:
        profile = await _yf.get_stock_profile(ticker)
        if not profile.name or profile.name == ticker:
            raise ValueError("Not a valid ticker")
    except Exception:
        # Fall back to SEC search to find a ticker
        ticker = None
        try:
            sec_results = await _sec.search_entities(entity_name)
            for r in sec_results:
                if r.ticker:
                    ticker = r.ticker
                    break
        except Exception as exc:
            errors.append(f"SEC entity search failed: {exc}")

    if not ticker:
        return ToolResponse(
            data={"error": f"Could not resolve '{entity_name}' to a stock ticker"},
            confidence=Confidence.LOW,
            sources=[
                SourceReference(name="yfinance"),
                SourceReference(name="SEC EDGAR", url="https://www.sec.gov/cgi-bin/browse-edgar"),
            ],
            errors=[f"No ticker found for '{entity_name}'"],
        ).model_dump()

    try:
        report = await build_exposure_report(ticker)
        data = report.model_dump()
        if report.us_institutional_holders:
            confidence = Confidence.HIGH
        else:
            confidence = Confidence.MEDIUM
    except Exception as exc:
        data = None
        errors.append(f"Exposure report failed: {exc}")
        confidence = Confidence.LOW

    return ToolResponse(
        data=data,
        confidence=confidence,
        sources=[
            SourceReference(name="yfinance", url=f"https://finance.yahoo.com/quote/{ticker}/holders"),
            SourceReference(name="SEC EDGAR", url="https://www.sec.gov"),
        ],
        errors=errors,
    ).model_dump()


@mcp.tool()
async def get_macro_indicator(series_id: str, period: str = "1y") -> dict:
    """Get a macroeconomic time series from FRED.

    Common series: VIXCLS (VIX), DGS10 (10-yr yield), DEXUSEU (EUR/USD),
    DTWEXBGS (trade-weighted dollar), CPIAUCSL (CPI), UNRATE (unemployment).

    Args:
        series_id: FRED series identifier (e.g. "VIXCLS").
        period: Lookback period — "1y", "6m", "5y", etc.
    """
    errors: list[str] = []
    try:
        series = await _fred.get_series(series_id, period=period)
        data = series.model_dump()
        confidence = Confidence.HIGH if series.observations else Confidence.LOW
    except Exception as exc:
        data = None
        errors.append(str(exc))
        confidence = Confidence.LOW

    return ToolResponse(
        data=data,
        confidence=confidence,
        sources=[SourceReference(
            name="FRED",
            url=f"https://fred.stlouisfed.org/series/{series_id.upper()}",
        )],
        errors=errors,
    ).model_dump()


@mcp.tool()
async def search_market_entity(query: str) -> dict:
    """Search for a company by name to find its ticker symbol and SEC CIK.

    Searches both yfinance and SEC EDGAR, returning a merged list of results.

    Args:
        query: Company name or partial ticker (e.g. "Taiwan Semiconductor").
    """
    errors: list[str] = []
    results: list[dict] = []

    # SEC EDGAR search
    try:
        sec_results = await _sec.search_entities(query)
        for r in sec_results:
            results.append(r.model_dump())
    except Exception as exc:
        errors.append(f"SEC search failed: {exc}")

    # FRED series search (if the query looks like a macro topic)
    fred_results: list[dict] = []
    try:
        fred_results = await _fred.search_series(query, limit=5)
    except Exception:
        pass  # FRED search is supplementary, not primary

    confidence = Confidence.HIGH if results else (Confidence.MEDIUM if fred_results else Confidence.LOW)

    return ToolResponse(
        data={
            "entities": results,
            "fred_series": fred_results,
        },
        confidence=confidence,
        sources=[
            SourceReference(name="SEC EDGAR", url="https://www.sec.gov"),
            SourceReference(name="FRED", url="https://fred.stlouisfed.org"),
        ],
        errors=errors,
    ).model_dump()


# Entry point for running as a standalone MCP server
if __name__ == "__main__":
    mcp.run()
