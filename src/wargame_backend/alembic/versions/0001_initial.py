"""Initial schema: all tables, enums, and indexes.

Revision ID: 0001
Revises: (none)
Create Date: 2026-04-15 00:00:00 UTC
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ------------------------------------------------------------------ #
    # 0. Extensions                                                        #
    # ------------------------------------------------------------------ #
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute('CREATE EXTENSION IF NOT EXISTS "uuid-ossp"')

    # ------------------------------------------------------------------ #
    # 1. Enum types                                                        #
    # ------------------------------------------------------------------ #
    # Use raw SQL with IF NOT EXISTS instead of postgresql.ENUM().create()
    # because the latter implicitly binds the enum to SQLAlchemy's MetaData
    # tracker, which then causes double-creation attempts when the enum is
    # referenced again by sa.Enum(name=..., create_type=False) in column
    # definitions below. Raw SQL bypasses the metadata magic entirely.
    op.execute(
        "CREATE TYPE relationship_posture AS ENUM "
        "('allied', 'friendly', 'neutral', 'tense', 'hostile')"
    )
    op.execute(
        "CREATE TYPE data_source_status AS ENUM "
        "('active', 'degraded', 'disabled')"
    )
    op.execute(
        "CREATE TYPE event_domain AS ENUM "
        "('info', 'diplomatic', 'economic', 'cyber', "
        "'kinetic_limited', 'kinetic_general')"
    )
    op.execute(
        "CREATE TYPE scenario_status AS ENUM "
        "('draft', 'ready', 'archived')"
    )
    op.execute(
        "CREATE TYPE simulation_status AS ENUM "
        "('pending', 'running', 'paused', 'completed', 'aborted', 'error')"
    )
    op.execute(
        "CREATE TYPE memory_type AS ENUM "
        "('observation', 'decision', 'intel', 'doctrine')"
    )

    # ------------------------------------------------------------------ #
    # 2. countries                                                         #
    # ------------------------------------------------------------------ #
    op.create_table(
        "countries",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("uuid_generate_v4()")),
        sa.Column("iso3", sa.Text, nullable=False),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("profile", postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column("doctrine", postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column("red_lines", postgresql.JSONB, nullable=False, server_default="[]"),
        sa.Column("military_assets", postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column("gdp_usd", sa.Numeric(precision=20, scale=2), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_countries_iso3", "countries", ["iso3"], unique=True)
    op.create_index(
        "ix_countries_profile_gin", "countries", ["profile"],
        postgresql_using="gin",
    )
    op.create_index(
        "ix_countries_doctrine_gin", "countries", ["doctrine"],
        postgresql_using="gin",
    )

    # ------------------------------------------------------------------ #
    # 3. relationships                                                     #
    # ------------------------------------------------------------------ #
    op.create_table(
        "relationships",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("uuid_generate_v4()")),
        sa.Column("country_a_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("countries.id", ondelete="CASCADE"), nullable=False),
        sa.Column("country_b_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("countries.id", ondelete="CASCADE"), nullable=False),
        sa.Column("posture",
                  postgresql.ENUM("allied", "friendly", "neutral", "tense", "hostile",
                                  name="relationship_posture", create_type=False),
                  nullable=False, server_default="neutral"),
        sa.Column("trust_score", sa.Integer,
                  nullable=False, server_default="0"),
        sa.CheckConstraint("trust_score BETWEEN -100 AND 100", name="ck_trust_score_range"),
        sa.Column("alliance_memberships", postgresql.JSONB,
                  nullable=False, server_default="[]"),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("country_a_id", "country_b_id", name="uq_relationships_pair"),
    )
    op.create_index("ix_relationships_country_a", "relationships", ["country_a_id"])
    op.create_index("ix_relationships_country_b", "relationships", ["country_b_id"])

    # ------------------------------------------------------------------ #
    # 4. data_sources                                                      #
    # ------------------------------------------------------------------ #
    op.create_table(
        "data_sources",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("uuid_generate_v4()")),
        sa.Column("source_key", sa.Text, nullable=False),
        sa.Column("display_name", sa.Text, nullable=False),
        sa.Column("last_ingest_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status",
                  postgresql.ENUM("active", "degraded", "disabled",
                                  name="data_source_status", create_type=False),
                  nullable=False, server_default="active"),
        sa.Column("records_ingested", sa.Integer, nullable=False, server_default="0"),
        sa.Column("metadata", postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_data_sources_source_key", "data_sources", ["source_key"], unique=True)

    # ------------------------------------------------------------------ #
    # 5. events                                                            #
    # ------------------------------------------------------------------ #
    op.create_table(
        "events",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("uuid_generate_v4()")),
        sa.Column("data_source_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("data_sources.id", ondelete="SET NULL"), nullable=True),
        sa.Column("source", sa.Text, nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("actor_iso3", sa.Text, nullable=True),
        sa.Column("target_iso3", sa.Text, nullable=True),
        sa.Column("event_type", sa.Text, nullable=True),
        sa.Column("domain", postgresql.ENUM(
            "info", "diplomatic", "economic", "cyber",
            "kinetic_limited", "kinetic_general",
            name="event_domain", create_type=False,
        ), nullable=True),
        sa.Column("severity", sa.Numeric(precision=5, scale=2), nullable=True),
        sa.Column("payload", postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column("raw_text", sa.Text, nullable=True),
        sa.Column("ingested_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
    )
    # Composite index for source+time window queries (primary access pattern for ingest workers)
    op.create_index("ix_events_source_occurred_at", "events", ["source", "occurred_at"])
    op.create_index(
        "ix_events_payload_gin", "events", ["payload"],
        postgresql_using="gin",
    )
    op.create_index("ix_events_actor_target", "events", ["actor_iso3", "target_iso3"])
    op.create_index("ix_events_data_source_id", "events", ["data_source_id"])
    op.create_index("ix_events_domain", "events", ["domain"])

    # ------------------------------------------------------------------ #
    # 6. scenarios                                                         #
    # ------------------------------------------------------------------ #
    op.create_table(
        "scenarios",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("uuid_generate_v4()")),
        sa.Column("title", sa.Text, nullable=False),
        sa.Column("description", sa.Text, nullable=False, server_default=""),
        sa.Column("country_ids", postgresql.JSONB, nullable=False, server_default="[]"),
        sa.Column("initial_conditions", postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column("status",
                  postgresql.ENUM("draft", "ready", "archived",
                                  name="scenario_status", create_type=False),
                  nullable=False, server_default="ready"),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_scenarios_status", "scenarios", ["status"])

    # ------------------------------------------------------------------ #
    # 7. simulations                                                       #
    # ------------------------------------------------------------------ #
    op.create_table(
        "simulations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("uuid_generate_v4()")),
        sa.Column("scenario_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("scenarios.id", ondelete="CASCADE"), nullable=False),
        sa.Column("status", postgresql.ENUM(
            "pending", "running", "paused", "completed", "aborted", "error",
            name="simulation_status", create_type=False,
        ), nullable=False, server_default="pending"),
        sa.Column("current_turn", sa.Integer, nullable=False, server_default="0"),
        sa.Column("max_turns", sa.Integer, nullable=False, server_default="20"),
        sa.Column("world_state_snapshot", postgresql.JSONB, nullable=True),
        sa.Column("config", postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_simulations_scenario_id", "simulations", ["scenario_id"])
    op.create_index("ix_simulations_status", "simulations", ["status"])

    # ------------------------------------------------------------------ #
    # 8. sim_events                                                        #
    # ------------------------------------------------------------------ #
    op.create_table(
        "sim_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("uuid_generate_v4()")),
        sa.Column("sim_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("simulations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("parent_event_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("sim_events.id", ondelete="SET NULL"), nullable=True),
        sa.Column("turn", sa.Integer, nullable=False),
        sa.Column("actor_country", sa.Text, nullable=False),
        sa.Column("target_country", sa.Text, nullable=True),
        sa.Column("domain", postgresql.ENUM(
            "info", "diplomatic", "economic", "cyber",
            "kinetic_limited", "kinetic_general",
            name="event_domain", create_type=False,
        ), nullable=False),
        sa.Column("action_type", sa.Text, nullable=False),
        sa.Column("payload", postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column("rationale", sa.Text, nullable=False, server_default=""),
        sa.Column("citations", postgresql.JSONB, nullable=False, server_default="[]"),
        sa.Column("escalation_rung", sa.Integer, nullable=False, server_default="0"),
        sa.Column("timestamp", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_sim_events_sim_turn", "sim_events", ["sim_id", "turn"])
    op.create_index("ix_sim_events_actor_domain", "sim_events", ["actor_country", "domain"])
    op.create_index("ix_sim_events_parent_event_id", "sim_events", ["parent_event_id"])
    op.create_index(
        "ix_sim_events_payload_gin", "sim_events", ["payload"],
        postgresql_using="gin",
    )

    # ------------------------------------------------------------------ #
    # 9. agent_memory                                                      #
    # ------------------------------------------------------------------ #
    op.create_table(
        "agent_memory",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("uuid_generate_v4()")),
        sa.Column("sim_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("simulations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("country_iso3", sa.Text, nullable=False),
        sa.Column("content", sa.Text, nullable=False),
        # voyage-3 embedding — 1536 dimensions
        sa.Column("embedding", sa.Text, nullable=False),  # placeholder; real type below
        sa.Column("memory_type", postgresql.ENUM(
            "observation", "decision", "intel", "doctrine",
            name="memory_type", create_type=False,
        ), nullable=False, server_default="observation"),
        sa.Column("turn", sa.Integer, nullable=False, server_default="0"),
        sa.Column("metadata", postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
    )

    # Replace Text placeholder with the real vector column
    op.execute("ALTER TABLE agent_memory ALTER COLUMN embedding TYPE vector(1536) USING embedding::vector(1536)")

    op.create_index("ix_agent_memory_sim_country", "agent_memory", ["sim_id", "country_iso3"])

    # HNSW index for approximate nearest-neighbor search (cosine similarity)
    # m=16 controls graph connectivity; ef_construction=64 controls build quality
    op.execute(
        "CREATE INDEX ix_agent_memory_embedding_hnsw "
        "ON agent_memory USING hnsw (embedding vector_cosine_ops) "
        "WITH (m = 16, ef_construction = 64)"
    )


def downgrade() -> None:
    # Drop tables in reverse dependency order
    op.drop_table("agent_memory")
    op.drop_table("sim_events")
    op.drop_table("simulations")
    op.drop_table("scenarios")
    op.drop_table("events")
    op.drop_table("data_sources")
    op.drop_table("relationships")
    op.drop_table("countries")

    # Drop enum types
    op.execute("DROP TYPE IF EXISTS memory_type")
    op.execute("DROP TYPE IF EXISTS simulation_status")
    op.execute("DROP TYPE IF EXISTS scenario_status")
    op.execute("DROP TYPE IF EXISTS event_domain")
    op.execute("DROP TYPE IF EXISTS data_source_status")
    op.execute("DROP TYPE IF EXISTS relationship_posture")
