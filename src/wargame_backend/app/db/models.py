"""SQLAlchemy 2.0 async ORM models for the Swarm wargame simulator.

All tables use UUID primary keys and TIMESTAMPTZ timestamps.
Vector columns use pgvector (voyage-3 embeddings, 1536 dims).
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from wargame_backend.app.db.base import Base

# ---------------------------------------------------------------------------
# Python-side enums (also registered as Postgres ENUM types via SQLAlchemy)
# ---------------------------------------------------------------------------


class RelationshipPosture(str, enum.Enum):
    """Bilateral relationship posture between two country agents."""

    allied = "allied"
    friendly = "friendly"
    neutral = "neutral"
    tense = "tense"
    hostile = "hostile"


class DataSourceStatus(str, enum.Enum):
    """Operational health of an ingestion source."""

    active = "active"
    degraded = "degraded"
    disabled = "disabled"


class EventDomain(str, enum.Enum):
    """Domain classification of a raw data-lake event or sim action."""

    info = "info"
    diplomatic = "diplomatic"
    economic = "economic"
    cyber = "cyber"
    kinetic_limited = "kinetic_limited"
    kinetic_general = "kinetic_general"


class ScenarioStatus(str, enum.Enum):
    """Lifecycle state of a user-authored scenario."""

    draft = "draft"
    ready = "ready"
    archived = "archived"


class SimulationStatus(str, enum.Enum):
    """Runtime state of a simulation run."""

    pending = "pending"
    running = "running"
    paused = "paused"
    completed = "completed"
    aborted = "aborted"
    error = "error"


class MemoryType(str, enum.Enum):
    """Category of an agent memory fragment."""

    observation = "observation"
    decision = "decision"
    intel = "intel"
    doctrine = "doctrine"


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class Country(Base):
    """Master registry of country agents.

    Holds static reference data and doctrine fields used to seed the
    country-agent LLM system prompt.
    """

    __tablename__ = "countries"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    iso3: Mapped[str] = mapped_column(Text, nullable=False, unique=True, index=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    # Free-form JSON blobs — GIN-indexed for containment queries
    profile: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    doctrine: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    red_lines: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    military_assets: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    # Markdown persona text (leadership, decision style, risk tolerance). Nullable
    # so rows predating the persona migration continue to load; the country-agent
    # prompt template falls back gracefully when persona is absent.
    persona: Mapped[str | None] = mapped_column(Text, nullable=True)
    gdp_usd: Mapped[float | None] = mapped_column(Numeric(precision=20, scale=2), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    # Relationships
    relationships_as_a: Mapped[list[CountryRelationship]] = relationship(
        "CountryRelationship",
        foreign_keys="CountryRelationship.country_a_id",
        back_populates="country_a",
        cascade="all, delete-orphan",
    )
    relationships_as_b: Mapped[list[CountryRelationship]] = relationship(
        "CountryRelationship",
        foreign_keys="CountryRelationship.country_b_id",
        back_populates="country_b",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        Index("ix_countries_profile_gin", "profile", postgresql_using="gin"),
        Index("ix_countries_doctrine_gin", "doctrine", postgresql_using="gin"),
    )


class CountryRelationship(Base):
    """Bilateral relationship state between any two country agents.

    UNIQUE constraint on (country_a_id, country_b_id) enforces one row
    per ordered pair. By convention country_a_id < country_b_id (UUID sort).
    """

    __tablename__ = "relationships"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    country_a_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("countries.id", ondelete="CASCADE"),
        nullable=False,
    )
    country_b_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("countries.id", ondelete="CASCADE"),
        nullable=False,
    )
    posture: Mapped[RelationshipPosture] = mapped_column(
        Enum(RelationshipPosture, name="relationship_posture"),
        nullable=False,
        default=RelationshipPosture.neutral,
    )
    # Trust score in range [-100, 100] int; canonical scale matches sim engine WorldState.
    # DB column is informational (runtime uses in-memory WorldState).
    trust_score: Mapped[int] = mapped_column(
        Integer,
        CheckConstraint("trust_score BETWEEN -100 AND 100", name="ck_trust_score_range"),
        nullable=False,
        default=0,
    )
    alliance_memberships: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    country_a: Mapped[Country] = relationship(
        "Country", foreign_keys=[country_a_id], back_populates="relationships_as_a"
    )
    country_b: Mapped[Country] = relationship(
        "Country", foreign_keys=[country_b_id], back_populates="relationships_as_b"
    )

    __table_args__ = (
        UniqueConstraint("country_a_id", "country_b_id", name="uq_relationships_pair"),
        Index("ix_relationships_country_a", "country_a_id"),
        Index("ix_relationships_country_b", "country_b_id"),
    )


class DataSource(Base):
    """Registry of every data ingestion source.

    Provides lineage (which source produced which events) and health
    monitoring for the nightly ingest workers.
    """

    __tablename__ = "data_sources"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    source_key: Mapped[str] = mapped_column(Text, nullable=False, unique=True, index=True)
    display_name: Mapped[str] = mapped_column(Text, nullable=False)
    last_ingest_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    status: Mapped[DataSourceStatus] = mapped_column(
        Enum(DataSourceStatus, name="data_source_status"),
        nullable=False,
        default=DataSourceStatus.active,
    )
    records_ingested: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    metadata_: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSONB, nullable=False, default=dict
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    events: Mapped[list[Event]] = relationship(
        "Event", back_populates="data_source", cascade="all, delete-orphan"
    )


class Event(Base):
    """Raw normalized event from the data lake.

    One row per real-world event regardless of origin. Serves as the
    read layer for agent RAG retrieval and the /api/events endpoint.
    """

    __tablename__ = "events"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    data_source_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("data_sources.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    source: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    actor_iso3: Mapped[str | None] = mapped_column(Text, nullable=True, index=True)
    target_iso3: Mapped[str | None] = mapped_column(Text, nullable=True, index=True)
    event_type: Mapped[str | None] = mapped_column(Text, nullable=True)
    domain: Mapped[EventDomain | None] = mapped_column(
        Enum(EventDomain, name="event_domain"), nullable=True, index=True
    )
    severity: Mapped[float | None] = mapped_column(
        Numeric(precision=5, scale=2), nullable=True
    )
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    raw_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    ingested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    data_source: Mapped[DataSource | None] = relationship(
        "DataSource", back_populates="events"
    )

    __table_args__ = (
        # Primary index for time-windowed queries by source (used by ingest workers)
        Index("ix_events_source_occurred_at", "source", "occurred_at"),
        # GIN index for JSONB containment / key-exists queries
        Index("ix_events_payload_gin", "payload", postgresql_using="gin"),
        Index("ix_events_actor_target", "actor_iso3", "target_iso3"),
    )


class Scenario(Base):
    """A user-authored "what-if" prompt.

    A scenario is the input specification; a simulation is the execution.
    One scenario may be run many times with different configs.
    """

    __tablename__ = "scenarios"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    title: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    country_ids: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    initial_conditions: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict
    )
    status: Mapped[ScenarioStatus] = mapped_column(
        Enum(ScenarioStatus, name="scenario_status"),
        nullable=False,
        default=ScenarioStatus.ready,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    simulations: Mapped[list[Simulation]] = relationship(
        "Simulation", back_populates="scenario", cascade="all, delete-orphan"
    )

    __table_args__ = (Index("ix_scenarios_status", "status"),)


class Simulation(Base):
    """A single execution run of a scenario.

    Created when the user hits POST /api/simulations.  The sim engine
    updates status, current_turn, and world_state_snapshot in place.
    """

    __tablename__ = "simulations"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    scenario_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("scenarios.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    status: Mapped[SimulationStatus] = mapped_column(
        Enum(SimulationStatus, name="simulation_status"),
        nullable=False,
        default=SimulationStatus.pending,
        index=True,
    )
    current_turn: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_turns: Mapped[int] = mapped_column(Integer, nullable=False, default=20)
    world_state_snapshot: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB, nullable=True
    )
    config: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    scenario: Mapped[Scenario] = relationship("Scenario", back_populates="simulations")
    sim_events: Mapped[list[SimEvent]] = relationship(
        "SimEvent", back_populates="simulation", cascade="all, delete-orphan"
    )
    agent_memories: Mapped[list[AgentMemory]] = relationship(
        "AgentMemory", back_populates="simulation", cascade="all, delete-orphan"
    )


class SimEvent(Base):
    """Crown-jewel event: every discrete action taken by a country agent.

    Streams over WebSocket → Redis PubSub → globe renderer.
    parent_event_id forms a tree of escalation chains within a simulation.
    """

    __tablename__ = "sim_events"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    sim_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("simulations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    parent_event_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("sim_events.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    turn: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    actor_country: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    target_country: Mapped[str | None] = mapped_column(Text, nullable=True, index=True)
    domain: Mapped[EventDomain] = mapped_column(
        Enum(EventDomain, name="event_domain"),
        nullable=False,
        index=True,
    )
    action_type: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    rationale: Mapped[str] = mapped_column(Text, nullable=False, default="")
    # citations: list of {"source": str, "ref": str}
    citations: Mapped[list[dict[str, str]]] = mapped_column(
        JSONB, nullable=False, default=list
    )
    # Integer rung 0..5; validated by EscalationRung Pydantic enum at write time
    escalation_rung: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # Structured "X did Y because Z in hopes of W" triplet. Nullable so
    # legacy rows and seed events render via the rationale-only fallback view.
    # Shape (when present): {summary, intended_outcome, triggering_factors:[
    #   {kind, ref, note, verified}, ...]}
    explainability: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB, nullable=True
    )
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    simulation: Mapped[Simulation] = relationship("Simulation", back_populates="sim_events")
    children: Mapped[list[SimEvent]] = relationship(
        "SimEvent",
        foreign_keys=[parent_event_id],
        back_populates="parent",
        cascade="all",
    )
    parent: Mapped[SimEvent | None] = relationship(
        "SimEvent",
        foreign_keys=[parent_event_id],
        back_populates="children",
        remote_side="SimEvent.id",
    )

    __table_args__ = (
        Index("ix_sim_events_sim_turn", "sim_id", "turn"),
        Index("ix_sim_events_actor_domain", "actor_country", "domain"),
        Index("ix_sim_events_payload_gin", "payload", postgresql_using="gin"),
    )


class AgentMemory(Base):
    """Per-country episodic memory with vector embedding for RAG.

    Each row is one memory fragment (decision, observation, intel) with a
    1536-dim Voyage-3 embedding for semantic retrieval during the PERCEIVE step.
    HNSW index (m=16, ef_construction=64) on the embedding column.
    """

    __tablename__ = "agent_memory"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    sim_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("simulations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    country_iso3: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    # voyage-3 embedding: 1536 dimensions
    embedding: Mapped[Any] = mapped_column(Vector(1536), nullable=False)
    memory_type: Mapped[MemoryType] = mapped_column(
        Enum(MemoryType, name="memory_type"),
        nullable=False,
        default=MemoryType.observation,
    )
    turn: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    metadata_: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSONB, nullable=False, default=dict
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    simulation: Mapped[Simulation] = relationship(
        "Simulation", back_populates="agent_memories"
    )

    __table_args__ = (
        Index("ix_agent_memory_sim_country", "sim_id", "country_iso3"),
        # HNSW index for fast approximate nearest-neighbor search
        # vector_cosine_ops is the operator class for cosine similarity
        Index(
            "ix_agent_memory_embedding_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_with={"m": 16, "ef_construction": 64},
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )


# Convenience re-export so `from app.db.models import *` works cleanly
__all__ = [
    "Base",
    "Country",
    "CountryRelationship",
    "DataSource",
    "Event",
    "Scenario",
    "Simulation",
    "SimEvent",
    "AgentMemory",
    "RelationshipPosture",
    "DataSourceStatus",
    "EventDomain",
    "ScenarioStatus",
    "SimulationStatus",
    "MemoryType",
]
