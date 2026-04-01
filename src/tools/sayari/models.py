"""Pydantic models for Sayari Graph Intelligence data."""

from __future__ import annotations

from pydantic import BaseModel, Field


class SayariOwnerLink(BaseModel):
    """One node in the beneficial ownership chain."""
    entity_id: str
    name: str
    entity_type: str = ""  # "company", "person"
    country: str | None = None
    ownership_percentage: float | None = None
    is_sanctioned: bool = False
    is_pep: bool = False
    depth: int = 0
    relationship_type: str = ""  # e.g. "registered_owner", "operator", "builder"
    parent_entity_id: str | None = None  # entity this link connects FROM


class SayariOwnershipChain(BaseModel):
    """Full UBO result for an entity."""
    vessel_entity_id: str | None = None
    owner_entity_id: str | None = None
    owner_name: str = ""
    chain: list[SayariOwnerLink] = Field(default_factory=list)
    max_depth_reached: int = 0


class SayariTradeRecord(BaseModel):
    """One shipment/trade record."""
    supplier: str = ""
    buyer: str = ""
    supplier_risks: list[str] = Field(default_factory=list)
    buyer_risks: list[str] = Field(default_factory=list)
    hs_code: str | None = None
    hs_description: str | None = None
    commodity_category: str = ""  # short label derived from HS code
    departure_country: str | None = None
    arrival_country: str | None = None
    date: str | None = None
    weight_kg: float | None = None
    value_usd: float | None = None


class SayariTradeActivity(BaseModel):
    """Aggregated trade data for an entity."""
    records: list[SayariTradeRecord] = Field(default_factory=list)
    top_hs_codes: list[dict[str, str]] = Field(default_factory=list)
    trade_countries: list[str] = Field(default_factory=list)
    record_count: int = 0
    sankey_flows: list[dict] = Field(default_factory=list)
    sankey_labels: dict[str, str] = Field(default_factory=dict)


class SayariVesselIntel(BaseModel):
    """Combined Sayari intelligence for a vessel."""
    resolved: bool = False
    owner_entity_id: str | None = None
    owner_name: str = ""
    ownership: SayariOwnershipChain | None = None
    trade: SayariTradeActivity | None = None
    risk_scores: dict[str, float] = Field(default_factory=dict)
