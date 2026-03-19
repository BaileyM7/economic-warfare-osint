"""MCP server exposing trade flow analysis tools.

Run standalone:
    uv run python -m src.tools.trade.server
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime

from mcp.server.fastmcp import FastMCP

from ...common.types import Confidence, SourceReference, ToolResponse
from .client import (
    get_bilateral_trade_flows,
    get_commodity_trade_flows,
    get_shipping_connectivity_data,
    get_supply_chain_dependency,
    get_trade_partner_summary,
    resolve_country,
)

logger = logging.getLogger(__name__)

mcp = FastMCP("trade")


def _comtrade_source() -> SourceReference:
    return SourceReference(
        name="UN Comtrade",
        url="https://comtradeapi.un.org",
        accessed_at=datetime.utcnow(),
    )


def _unctad_source() -> SourceReference:
    return SourceReference(
        name="UNCTADstat",
        url="https://unctadstat.unctad.org",
        accessed_at=datetime.utcnow(),
    )


def _assess_confidence(data_items: int, has_values: bool) -> Confidence:
    """Heuristic confidence based on data completeness."""
    if data_items == 0:
        return Confidence.LOW
    if has_values and data_items >= 3:
        return Confidence.HIGH
    return Confidence.MEDIUM


@mcp.tool()
async def get_bilateral_trade(
    reporter: str,
    partner: str,
    year: int = 2023,
) -> dict:
    """Get trade flows between two countries.

    Args:
        reporter: Reporting country (name, ISO-3 code, or common abbreviation)
        partner: Partner country (name, ISO-3 code, or common abbreviation)
        year: Trade data year (default 2023)

    Returns:
        ToolResponse with list of TradeFlow records showing imports and exports.
    """
    errors: list[str] = []
    try:
        flows = await get_bilateral_trade_flows(reporter, partner, year)
    except Exception as exc:
        logger.error("get_bilateral_trade error: %s", exc)
        flows = []
        errors.append(str(exc))

    flow_dicts = [f.model_dump() for f in flows]
    has_values = any(f.trade_value_usd > 0 for f in flows)

    response = ToolResponse(
        data={
            "reporter": resolve_country(reporter),
            "partner": resolve_country(partner),
            "year": year,
            "flows": flow_dicts,
            "total_records": len(flow_dicts),
        },
        confidence=_assess_confidence(len(flows), has_values),
        sources=[_comtrade_source()],
        errors=errors,
    )
    return response.model_dump()


@mcp.tool()
async def get_commodity_trade(
    commodity_code: str,
    reporter: str = "",
    year: int = 2023,
) -> dict:
    """Get trade data for a specific HS commodity code.

    Args:
        commodity_code: HS commodity code (e.g. '8542' for integrated circuits,
                        '2709' for crude petroleum, '7108' for gold)
        reporter: Optional reporting country to filter by (name or ISO-3)
        year: Trade data year (default 2023)

    Returns:
        ToolResponse with list of TradeFlow records for the commodity.
    """
    errors: list[str] = []
    try:
        flows = await get_commodity_trade_flows(commodity_code, reporter, year)
    except Exception as exc:
        logger.error("get_commodity_trade error: %s", exc)
        flows = []
        errors.append(str(exc))

    flow_dicts = [f.model_dump() for f in flows]
    has_values = any(f.trade_value_usd > 0 for f in flows)

    response = ToolResponse(
        data={
            "commodity_code": commodity_code,
            "reporter": resolve_country(reporter) if reporter else "multiple",
            "year": year,
            "flows": flow_dicts,
            "total_records": len(flow_dicts),
        },
        confidence=_assess_confidence(len(flows), has_values),
        sources=[_comtrade_source()],
        errors=errors,
    )
    return response.model_dump()


@mcp.tool()
async def get_supply_chain_exposure(
    country: str,
    commodity_code: str,
) -> dict:
    """Analyse how dependent a country is on imports of a specific commodity.

    Shows import concentration risk: what share of total imports this commodity
    represents, and which suppliers dominate.

    Args:
        country: Country to analyse (name or ISO-3 code)
        commodity_code: HS commodity code (e.g. '8542' for semiconductors,
                        '2844' for radioactive elements, '2709' for crude oil)

    Returns:
        ToolResponse with CommodityDependency data including import share
        percentage and ranked list of top suppliers.
    """
    errors: list[str] = []
    try:
        dep = await get_supply_chain_dependency(country, commodity_code)
    except Exception as exc:
        logger.error("get_supply_chain_exposure error: %s", exc)
        dep = None
        errors.append(str(exc))

    if dep:
        data = dep.model_dump()
        data["country"] = resolve_country(country)
        has_values = dep.import_share_pct > 0 or len(dep.top_suppliers) > 0
        confidence = _assess_confidence(len(dep.top_suppliers), has_values)
    else:
        data = {
            "country": resolve_country(country),
            "commodity_code": commodity_code,
            "commodity_desc": "",
            "import_share_pct": 0.0,
            "top_suppliers": [],
        }
        confidence = Confidence.LOW

    response = ToolResponse(
        data=data,
        confidence=confidence,
        sources=[_comtrade_source()],
        errors=errors,
    )
    return response.model_dump()


@mcp.tool()
async def get_trade_partners(
    country: str,
    flow: str = "import",
    year: int = 2023,
) -> dict:
    """Get top trade partners for a country.

    Args:
        country: Country to analyse (name or ISO-3 code)
        flow: Trade direction — 'import' or 'export'
        year: Trade data year (default 2023)

    Returns:
        ToolResponse with TradePartnerSummary including total values and
        ranked lists of top partners and commodities.
    """
    errors: list[str] = []
    try:
        summary = await get_trade_partner_summary(country, flow, year)
    except Exception as exc:
        logger.error("get_trade_partners error: %s", exc)
        summary = None
        errors.append(str(exc))

    if summary:
        data = summary.model_dump()
        total = summary.total_imports_usd + summary.total_exports_usd
        has_values = total > 0
        confidence = _assess_confidence(
            len(summary.top_commodities), has_values
        )
    else:
        data = {
            "country": resolve_country(country),
            "total_imports_usd": 0.0,
            "total_exports_usd": 0.0,
            "top_commodities": [],
        }
        confidence = Confidence.LOW

    response = ToolResponse(
        data=data,
        confidence=confidence,
        sources=[_comtrade_source()],
        errors=errors,
    )
    return response.model_dump()


@mcp.tool()
async def get_shipping_connectivity(country: str) -> dict:
    """Get the UNCTAD Liner Shipping Connectivity Index (LSCI) for a country.

    The LSCI captures how well a country is connected to global shipping
    networks. Higher scores indicate better maritime transport connectivity.

    Args:
        country: Country to look up (name or ISO-3 code)

    Returns:
        ToolResponse with ShippingConnectivity data including LSCI score
        and top bilateral shipping connections.
    """
    errors: list[str] = []
    try:
        conn = await get_shipping_connectivity_data(country)
    except Exception as exc:
        logger.error("get_shipping_connectivity error: %s", exc)
        conn = None
        errors.append(str(exc))

    if conn:
        data = conn.model_dump()
        has_values = conn.lsci_score is not None
        confidence = _assess_confidence(
            len(conn.bilateral_connections) + (1 if has_values else 0),
            has_values,
        )
    else:
        data = {
            "country": resolve_country(country),
            "lsci_score": None,
            "year": 2023,
            "bilateral_connections": [],
        }
        confidence = Confidence.LOW

    response = ToolResponse(
        data=data,
        confidence=confidence,
        sources=[_unctad_source()],
        errors=errors,
    )
    return response.model_dump()


if __name__ == "__main__":
    mcp.run()
