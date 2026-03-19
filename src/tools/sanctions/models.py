"""Pydantic models for sanctions and watchlist data."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class SanctionEntry(BaseModel):
    """A single entry on a sanctions or watchlist."""

    id: str
    name: str
    aliases: list[str] = Field(default_factory=list)
    entity_type: str = "unknown"  # "person", "company", "vessel", "aircraft", "unknown"
    programs: list[str] = Field(default_factory=list)
    addresses: list[str] = Field(default_factory=list)
    identifiers: dict[str, str] = Field(
        default_factory=dict
    )  # e.g. {"passport": "...", "tax_id": "..."}
    list_source: str = ""  # "OFAC SDN", "OpenSanctions", "EU", etc.
    designation_date: datetime | None = None
    remarks: str | None = None
    score: float | None = None  # match/relevance score from search


class SanctionSearchResult(BaseModel):
    """Aggregated results from searching across multiple sanctions lists."""

    query: str
    matches: list[SanctionEntry] = Field(default_factory=list)
    total_matches: int = 0
    proximity_score: float | None = None  # degrees of separation (0 = direct match)


class SanctionStatus(BaseModel):
    """Whether a specific entity is sanctioned and on which lists."""

    entity_name: str
    is_sanctioned: bool = False
    lists_found: list[str] = Field(default_factory=list)
    designation_dates: list[datetime | None] = Field(default_factory=list)
    programs: list[str] = Field(default_factory=list)
    entries: list[SanctionEntry] = Field(default_factory=list)


class ProximityNode(BaseModel):
    """A node in the sanctions proximity graph."""

    entity_id: str
    entity_name: str
    entity_type: str = "unknown"
    is_sanctioned: bool = False
    sanctions_lists: list[str] = Field(default_factory=list)
    hop_distance: int = 0


class ProximityEdge(BaseModel):
    """An edge in the sanctions proximity graph."""

    source_id: str
    target_id: str
    relationship_type: str = "associated"
    properties: dict[str, Any] = Field(default_factory=dict)


class ProximityResult(BaseModel):
    """Result of a sanctions proximity/degrees-of-separation search."""

    query_entity: str
    nodes: list[ProximityNode] = Field(default_factory=list)
    edges: list[ProximityEdge] = Field(default_factory=list)
    nearest_sanctioned_hop: int | None = None  # None = no sanctioned entity found
    sanctioned_neighbors: list[ProximityNode] = Field(default_factory=list)


class RecentDesignation(BaseModel):
    """A recently designated entity from OFAC or other lists."""

    entry: SanctionEntry
    action_type: str = "designation"  # "designation", "removal", "update"
    effective_date: datetime | None = None
    federal_register_notice: str | None = None
