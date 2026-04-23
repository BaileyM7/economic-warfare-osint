"""SimEvent — the crown-jewel event schema for the Swarm wargame simulator.

Every discrete action taken by a country agent in a simulation turn is
represented as a SimEvent.  These objects:
  - are written to the `sim_events` Postgres table
  - are published to the Redis PubSub channel `sim:{sim_id}:events`
  - are forwarded via WebSocket to the globe renderer as `sim_event` frames
  - drive the arc animations on the Deck.gl globe (domain → color mapping)
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import IntEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


import enum as _enum


class Domain(str, _enum.Enum):
    info = "info"                            # violet  #9333ea
    diplomatic = "diplomatic"               # blue    #3b82f6
    economic = "economic"                   # amber   #f59e0b
    cyber = "cyber"                          # cyan    #06b6d4
    kinetic_limited = "kinetic_limited"     # orange  #f97316
    kinetic_general = "kinetic_general"     # red     #ef4444


class EscalationRung(IntEnum):
    """Escalation ladder — integer rung 0..5.

    The arbiter assigns a rung to every SimEvent after adjudication.
    The frontend color-saturates arcs by rung value.
    """

    peacetime = 0           # Normal diplomatic interactions
    gray_zone = 1           # Sub-threshold covert ops, disinformation
    coercive_diplomacy = 2  # Explicit threats, military exercises near borders
    limited_conflict = 3    # Blockades, limited strikes, cyber attacks on critical infra
    regional_war = 4        # Sustained military engagement, multiple actors
    general_war = 5         # Full-scale multi-domain conflict, nuclear signaling


class Citation(BaseModel):
    """Reference to a data-lake event that the agent cited as evidence."""

    model_config = ConfigDict(frozen=True)

    source: str = Field(
        ...,
        description="Source key, e.g. 'gdelt', 'acled', 'worldbank'.",
        examples=["gdelt"],
    )
    ref: str = Field(
        ...,
        description="Source-specific event reference ID.",
        examples=["GDELT-20270114-CHN-TWN-094"],
    )


# ---------------------------------------------------------------------------
# Explainability — structured "why did the agent do this?" triplet
# ---------------------------------------------------------------------------


class FactorKind(str, _enum.Enum):
    """The class of evidence the agent is citing as a triggering factor.

    Each kind dictates how ``ref`` is resolved by the frontend:
      * ``event``      — ref is a SimEvent UUID drawn from this agent's perception
      * ``red_line``   — ref is a red-line slug (or first 6 words of description)
      * ``memory``     — ref is ``"turn:<N>"`` pointing at a recalled memory turn
      * ``posture``    — ref is an ordered country-pair like ``"USA-TWN"``
      * ``perception`` — ref is a dotted field path within the perception summary
    """

    event = "event"
    red_line = "red_line"
    memory = "memory"
    posture = "posture"
    perception = "perception"


class TriggeringFactor(BaseModel):
    """One concrete piece of evidence an agent cited when deciding to act.

    The discriminated ``kind`` + ``ref`` shape lets the frontend resolve each
    factor back to the exact source object — clickable causes, not narration.
    """

    model_config = ConfigDict(str_strip_whitespace=True)

    kind: FactorKind = Field(..., description="The class of evidence cited.")
    ref: str = Field(
        ...,
        min_length=1,
        max_length=200,
        description=(
            "Reference whose interpretation depends on `kind`: "
            "event UUID, red-line slug, 'turn:N', ISO3 pair, or perception field path."
        ),
    )
    note: str = Field(
        ...,
        min_length=1,
        max_length=200,
        description="One short clause naming what about this factor drove the choice.",
    )
    verified: bool = Field(
        default=True,
        description=(
            "False if the SimLoop could not verify ``ref`` against the agent's "
            "perception (e.g. a `kind=event` ref that did not appear in "
            "recent_events). The factor is preserved but flagged for the UI."
        ),
    )


class Explainability(BaseModel):
    """Structured explanation of a single agent decision.

    Reads as: the agent did *summary* because of *triggering_factors* in hopes
    of *intended_outcome*.

    Optional on the SimEvent so legacy events (pre-feature) and seed events
    can render via the legacy rationale-only view.
    """

    model_config = ConfigDict(str_strip_whitespace=True)

    summary: str = Field(
        ...,
        min_length=1,
        max_length=160,
        description="One verb phrase naming the action. e.g. 'Imposed targeted sanctions on TSMC exports.'",
    )
    triggering_factors: list[TriggeringFactor] = Field(
        ...,
        min_length=1,
        max_length=4,
        description="1–4 evidentiary factors the agent reacted to.",
    )
    intended_outcome: str = Field(
        ...,
        min_length=1,
        max_length=240,
        description="One sentence stating the result the agent hopes to cause.",
    )


class SimEventCreate(BaseModel):
    """Input schema used by the sim engine when writing a new SimEvent.

    The `id` and `timestamp` fields are generated server-side; they are
    not part of the creation payload.
    """

    model_config = ConfigDict(
        populate_by_name=True,
        str_strip_whitespace=True,
    )

    sim_id: uuid.UUID = Field(..., description="Owning simulation UUID.")
    parent_event_id: uuid.UUID | None = Field(
        default=None,
        description="Parent SimEvent UUID if this action is a direct response.",
    )
    turn: int = Field(..., ge=0, description="0-indexed turn counter.")
    actor_country: str = Field(
        ...,
        min_length=3,
        max_length=3,
        description="ISO-3 code of the acting country agent.",
        examples=["CHN"],
    )
    target_country: str | None = Field(
        default=None,
        min_length=3,
        max_length=3,
        description="ISO-3 code of the target country. Null for broadcast/unilateral actions.",
        examples=["TWN"],
    )
    domain: Domain = Field(..., description="Domain classification of the action.")
    action_type: str = Field(
        ...,
        min_length=1,
        max_length=128,
        description="Verb-phrase slug describing the action, e.g. 'naval_blockade_declaration'.",
        examples=["naval_blockade_declaration"],
    )
    payload: dict[str, Any] = Field(
        default_factory=dict,
        description="Domain-specific structured details (free-form JSON).",
    )
    rationale: str = Field(
        default="",
        description="LLM chain-of-thought reasoning string shown in the AgentDrawer.",
    )
    citations: list[Citation] = Field(
        default_factory=list,
        description="Data-lake events cited as evidence by the agent.",
    )
    escalation_rung: int = Field(
        default=0,
        ge=0,
        le=5,
        description="Escalation ladder rung (0=peacetime, 5=general_war).",
    )
    explainability: Explainability | None = Field(
        default=None,
        description=(
            "Structured 'X did Y because Z in hopes of W' triplet. None for "
            "legacy events and scenario seeds; populated for every agent-driven "
            "SimEvent emitted by the SimLoop."
        ),
    )

    @field_validator("actor_country", "target_country", mode="before")
    @classmethod
    def upper_iso3(cls, v: str | None) -> str | None:
        """Normalise ISO-3 codes to uppercase."""
        return v.upper() if v else v

    @model_validator(mode="after")
    def actor_target_differ(self) -> SimEventCreate:
        """An actor cannot target itself."""
        if self.target_country and self.target_country == self.actor_country:
            raise ValueError("actor_country and target_country must differ")
        return self


class SimEvent(SimEventCreate):
    """Full SimEvent as stored in the database and streamed over WebSocket.

    Adds server-generated fields (`id`, `timestamp`) to the creation schema.
    """

    model_config = ConfigDict(
        from_attributes=True,   # Enable ORM-mode for SQLAlchemy model hydration
        populate_by_name=True,
        str_strip_whitespace=True,
    )

    id: uuid.UUID = Field(
        default_factory=uuid.uuid4,
        description="Globally unique event ID.",
    )
    timestamp: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="Wall-clock UTC time when this event was emitted.",
    )
