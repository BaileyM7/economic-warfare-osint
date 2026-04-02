"""Pydantic models for Sayari API responses."""

from __future__ import annotations

from pydantic import BaseModel, Field


class SayariEntity(BaseModel):
    """A resolved or traversed entity from Sayari."""

    entity_id: str
    label: str
    type: str = ""
    country: str | None = None
    addresses: list[str] = Field(default_factory=list)
    identifiers: list[str] = Field(default_factory=list)
    sources: list[str] = Field(default_factory=list)
    pep: bool = False
    sanctioned: bool = False


class SayariRelationship(BaseModel):
    """An edge between two Sayari entities."""

    source_id: str
    target_id: str
    relationship_type: str
    attributes: dict[str, str] = Field(default_factory=dict)


class SayariResolveResult(BaseModel):
    """Result of entity resolution."""

    entities: list[SayariEntity]
    query: str


class SayariTraversalResult(BaseModel):
    """Result of graph traversal from a given entity."""

    root_id: str
    entities: list[SayariEntity]
    relationships: list[SayariRelationship]


class SayariUBOOwner(BaseModel):
    """A single beneficial owner from the UBO endpoint."""

    entity_id: str
    name: str
    type: str = ""
    country: str | None = None
    ownership_percentage: float | None = None
    path_length: int = 1
    sanctioned: bool = False
    pep: bool = False


class SayariUBOResult(BaseModel):
    """Result of UBO (Ultimate Beneficial Ownership) lookup."""

    target_id: str
    target_name: str
    owners: list[SayariUBOOwner]


# ---------------------------------------------------------------------------
# Vessel intelligence models (used by get_vessel_intel)
# ---------------------------------------------------------------------------


class SayariOwnerLink(BaseModel):
    """One node in the beneficial ownership chain."""

    entity_id: str
    name: str
    entity_type: str = ""
    country: str | None = None
    ownership_percentage: float | None = None
    is_sanctioned: bool = False
    is_pep: bool = False
    depth: int = 0
    relationship_type: str = ""
    parent_entity_id: str | None = None


class SayariOwnershipChain(BaseModel):
    """Full UBO result for an entity, rooted at the vessel."""

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
    commodity_category: str = ""
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
