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
