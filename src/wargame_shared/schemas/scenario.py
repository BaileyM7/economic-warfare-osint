"""Pydantic v2 request/response schemas for the Scenarios API.

POST /api/scenarios   → ScenarioCreate (request)  → ScenarioResponse (response)
GET  /api/scenarios   → ScenarioList (response)
GET  /api/scenarios/{id} → ScenarioResponse (response)
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ScenarioStatus(str, Enum):
    """Lifecycle state of a user-authored scenario."""

    draft = "draft"
    ready = "ready"
    archived = "archived"


class SeedEvent(BaseModel):
    """A single scenario-defined seed event injected at turn 0.

    Canonical fields only; unknown keys are rejected (model_config forbid).
    """

    model_config = ConfigDict(extra="forbid")

    actor_country: str = Field(..., max_length=3, description="ISO-3 actor country code.")
    target_country: str | None = Field(None, max_length=3)
    domain: str = Field(default="info", max_length=32)
    action_type: str = Field(default="scenario_seed", max_length=64)
    rationale: str = Field(default="Scenario seed event.", max_length=1000)
    payload: dict[str, Any] = Field(default_factory=dict, description="Domain-specific details.")
    escalation_rung: int = Field(default=2, ge=0, le=5)


class InitialConditions(BaseModel):
    """Optional overrides applied to the world state at turn 0."""

    model_config = ConfigDict(extra="forbid")

    posture_overrides: dict[str, str] = Field(
        default_factory=dict,
        description="Map of ISO-3 → posture override string for scenario start.",
        examples=[{"CHN": "aggressive", "USA": "deterrent"}],
    )
    seed_events: list[SeedEvent] = Field(
        default_factory=list,
        description="Pre-seeded events injected into the world state at turn 0.",
    )


class ScenarioCreate(BaseModel):
    """Request body for POST /api/scenarios."""

    model_config = ConfigDict(str_strip_whitespace=True)

    title: str = Field(
        ...,
        min_length=1,
        max_length=256,
        description="Human-readable scenario title.",
        examples=["China–Taiwan Blockade 2027"],
    )
    description: str = Field(
        default="",
        max_length=8192,
        description="Free-form scenario description / user prompt.",
        examples=["China initiates a quarantine blockade of Taiwan strait."],
    )
    country_ids: list[str] = Field(
        ...,
        min_length=2,
        description="ISO-3 codes of countries participating in this scenario.",
        examples=[["CHN", "TWN", "USA", "JPN", "KOR", "PHL", "AUS", "PRK", "RUS", "IND"]],
    )
    initial_conditions: InitialConditions = Field(
        default_factory=InitialConditions,
        description="Optional world-state overrides at turn 0.",
    )

    @field_validator("country_ids", mode="before")
    @classmethod
    def normalise_and_deduplicate(cls, v: list[str]) -> list[str]:
        """Uppercase and deduplicate ISO-3 codes while preserving order."""
        seen: set[str] = set()
        result: list[str] = []
        for code in v:
            upper = code.upper()
            if upper not in seen:
                seen.add(upper)
                result.append(upper)
        return result

    @field_validator("country_ids")
    @classmethod
    def min_two_countries(cls, v: list[str]) -> list[str]:
        if len(v) < 2:
            raise ValueError("A scenario requires at least 2 countries.")
        return v


class ScenarioResponse(BaseModel):
    """Full scenario representation returned by POST and GET /api/scenarios/{id}."""

    model_config = ConfigDict(
        from_attributes=True,
        populate_by_name=True,
    )

    id: uuid.UUID = Field(..., description="Scenario UUID.")
    title: str
    description: str
    country_ids: list[str]
    initial_conditions: dict[str, Any] = Field(
        default_factory=dict,
        description="Stored initial conditions JSON.",
    )
    status: ScenarioStatus
    created_at: datetime
    updated_at: datetime


class ScenarioListItem(BaseModel):
    """Lightweight scenario summary for GET /api/scenarios list."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    title: str
    status: ScenarioStatus
    country_ids: list[str]
    created_at: datetime


class ScenarioList(BaseModel):
    """Paginated list response for GET /api/scenarios."""

    items: list[ScenarioListItem]
    total: int = Field(..., ge=0)
    limit: int = Field(..., ge=1, le=200)
    offset: int = Field(..., ge=0)
