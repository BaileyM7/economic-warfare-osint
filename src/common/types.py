"""Shared types used across all layers of the system."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class Confidence(str, Enum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


class SourceReference(BaseModel):
    """Provenance tracking for a data point."""
    name: str  # e.g. "OpenSanctions", "OFAC SDN"
    url: str | None = None
    accessed_at: datetime = Field(default_factory=datetime.utcnow)
    dataset_version: str | None = None


class ToolResponse(BaseModel):
    """Standard envelope returned by every MCP tool.

    The orchestrator always receives this shape — it never sees raw API responses.
    """
    data: Any  # tool-specific typed payload
    confidence: Confidence
    sources: list[SourceReference]
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    errors: list[str] = Field(default_factory=list)


# --- Entity models used across tools ---

class Entity(BaseModel):
    """A resolved real-world entity (company, person, vessel, etc.)."""
    id: str
    name: str
    entity_type: str  # "company", "person", "vessel", "government"
    aliases: list[str] = Field(default_factory=list)
    country: str | None = None
    identifiers: dict[str, str] = Field(default_factory=dict)  # e.g. {"lei": "...", "ofac_id": "..."}


class Relationship(BaseModel):
    """A directed edge between two entities."""
    source_id: str
    target_id: str
    relationship_type: str  # "subsidiary_of", "beneficial_owner", "supplies", "trades_with"
    properties: dict[str, Any] = Field(default_factory=dict)
    confidence: Confidence = Confidence.MEDIUM
    sources: list[SourceReference] = Field(default_factory=list)


class EntityGraph(BaseModel):
    """A collection of entities and their relationships."""
    entities: list[Entity] = Field(default_factory=list)
    relationships: list[Relationship] = Field(default_factory=list)

    def add_entity(self, entity: Entity) -> None:
        if not any(e.id == entity.id for e in self.entities):
            self.entities.append(entity)

    def add_relationship(self, rel: Relationship) -> None:
        self.relationships.append(rel)

    def merge(self, other: EntityGraph) -> None:
        for entity in other.entities:
            self.add_entity(entity)
        for rel in other.relationships:
            self.add_relationship(rel)


class ScenarioType(str, Enum):
    SANCTION_IMPACT = "sanction_impact"
    SUPPLY_CHAIN_DISRUPTION = "supply_chain_disruption"
    INVESTMENT_INTERCEPTION = "investment_interception"
    FACILITY_DENIAL = "facility_denial"
    TRADE_DISRUPTION = "trade_disruption"


class AnalystQuery(BaseModel):
    """Parsed representation of the analyst's original question."""
    raw_query: str
    scenario_type: ScenarioType | None = None
    target_entities: list[str] = Field(default_factory=list)
    parameters: dict[str, Any] = Field(default_factory=dict)


class ImpactAssessment(BaseModel):
    """Final synthesized output from the orchestrator."""
    query: AnalystQuery
    scenario_type: ScenarioType
    executive_summary: str
    findings: list[dict[str, Any]]
    friendly_fire: list[dict[str, Any]] = Field(default_factory=list)
    entity_graph: EntityGraph = Field(default_factory=EntityGraph)
    confidence_summary: dict[str, Confidence] = Field(default_factory=dict)
    sources: list[SourceReference] = Field(default_factory=list)
    recommendations: list[str] = Field(default_factory=list)
    tool_results: dict[str, Any] = Field(default_factory=dict)
