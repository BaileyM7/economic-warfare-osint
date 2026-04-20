"""Pydantic models for corporate graph data (OpenCorporates, GLEIF, ICIJ)."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, Field


class Officer(BaseModel):
    """A company officer (director, secretary, etc.)."""
    name: str
    role: str  # "director", "secretary", "agent", etc.
    start_date: date | None = None
    end_date: date | None = None
    nationality: str | None = None
    # Optional linkage back to the company this officer record came from.
    # Populated by /officers/search hits where the company is embedded;
    # left None when the officer record is fetched standalone.
    company_name: str | None = None
    company_jurisdiction: str | None = None
    company_number: str | None = None
    company_url: str | None = None


class CompanyRecord(BaseModel):
    """A company as returned by OpenCorporates."""
    name: str
    jurisdiction: str  # ISO 3166-1 alpha-2 or OpenCorporates jurisdiction code
    company_number: str
    status: str | None = None  # "Active", "Dissolved", etc.
    incorporation_date: date | None = None
    registered_address: str | None = None
    officers: list[Officer] = Field(default_factory=list)
    industry_codes: list[dict[str, str]] = Field(default_factory=list)  # [{"code": "6201", "scheme": "SIC"}]
    opencorporates_url: str | None = None


class OwnershipLink(BaseModel):
    """An ownership relationship between two entities."""
    parent_id: str  # LEI or company identifier
    child_id: str
    ownership_pct: float | None = None
    relationship_type: str  # "direct_parent", "ultimate_parent", "subsidiary"


class LEIRecord(BaseModel):
    """A Legal Entity Identifier record from GLEIF."""
    lei: str
    legal_name: str
    country: str | None = None
    jurisdiction: str | None = None
    status: str | None = None  # "ACTIVE", "LAPSED", etc.
    parent_lei: str | None = None
    ultimate_parent_lei: str | None = None
    registration_date: date | None = None
    last_update: date | None = None


class OffshoreEntity(BaseModel):
    """An entity from the ICIJ Offshore Leaks database."""
    node_id: str
    name: str
    jurisdiction: str | None = None
    source_dataset: str | None = None  # "Panama Papers", "Pandora Papers", etc.
    address: str | None = None
    linked_to: list[str] = Field(default_factory=list)  # names of linked entities
    note: str | None = None


class CorporateSearchResult(BaseModel):
    """Unified search result across all corporate data sources."""
    companies: list[CompanyRecord] = Field(default_factory=list)
    lei_records: list[LEIRecord] = Field(default_factory=list)
    offshore_entities: list[OffshoreEntity] = Field(default_factory=list)


class CorporateTree(BaseModel):
    """Ownership tree for an entity."""
    entity_name: str
    ownership_links: list[OwnershipLink] = Field(default_factory=list)
    companies: list[CompanyRecord] = Field(default_factory=list)
    lei_records: list[LEIRecord] = Field(default_factory=list)


class BeneficialOwnerResult(BaseModel):
    """Officers and beneficial ownership information."""
    entity_name: str
    officers: list[Officer] = Field(default_factory=list)
    offshore_connections: list[OffshoreEntity] = Field(default_factory=list)
    companies: list[CompanyRecord] = Field(default_factory=list)


class OffshoreConnectionResult(BaseModel):
    """Offshore connections found for an entity."""
    entity_name: str
    entities: list[OffshoreEntity] = Field(default_factory=list)


class ResolvedEntity(BaseModel):
    """An entity resolved across multiple sources."""
    name: str
    jurisdiction: str | None = None
    company_records: list[CompanyRecord] = Field(default_factory=list)
    lei_records: list[LEIRecord] = Field(default_factory=list)
    offshore_entities: list[OffshoreEntity] = Field(default_factory=list)
