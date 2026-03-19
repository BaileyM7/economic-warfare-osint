"""Pydantic models for trade flow, dependency, and shipping connectivity data."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class TradeFlow(BaseModel):
    """A single trade flow record between two countries for a commodity."""

    reporter_country: str
    partner_country: str
    commodity_code: str
    commodity_desc: str = ""
    trade_value_usd: float = 0.0
    weight_kg: float | None = None
    year: int = 2023
    flow_type: Literal["import", "export"] = "import"


class TradePartnerSummary(BaseModel):
    """Aggregated trade summary for a country."""

    country: str
    total_imports_usd: float = 0.0
    total_exports_usd: float = 0.0
    top_commodities: list[dict[str, str | float]] = Field(default_factory=list)


class CommodityDependency(BaseModel):
    """How dependent a country is on imports of a specific commodity."""

    commodity_code: str
    commodity_desc: str = ""
    import_share_pct: float = 0.0
    top_suppliers: list[dict[str, str | float]] = Field(default_factory=list)


class BilateralConnection(BaseModel):
    """A single bilateral shipping connection entry."""

    partner_country: str
    lsci_bilateral: float | None = None


class ShippingConnectivity(BaseModel):
    """UNCTAD Liner Shipping Connectivity Index data for a country."""

    country: str
    lsci_score: float | None = None
    year: int = 2023
    bilateral_connections: list[BilateralConnection] = Field(default_factory=list)
