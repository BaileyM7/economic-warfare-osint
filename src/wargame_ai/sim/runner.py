"""Concrete SimRunner implementation backed by the LangGraph :class:`SimLoop`.

This module is imported lazily from ``app.sim_runner.build_sim_runner`` when
``AGENT_RUNNER_IMPL=langgraph``.  Keeping it separate from ``loop.py`` lets
unit tests exercise the turn loop without the SQLAlchemy / Anthropic import
graph.
"""

from __future__ import annotations

import asyncio
import uuid
from pathlib import Path
from typing import Any

import structlog
import yaml
from sqlalchemy import select

from wargame_backend.app.config import get_settings
from wargame_backend.app.db.models import Scenario as ScenarioORM
from wargame_backend.app.db.models import Simulation as SimulationORM
from wargame_backend.app.db.models import SimulationStatus
from wargame_backend.app.db.session import AsyncSessionLocal

from wargame_ai.agents.arbiter import Arbiter
from wargame_ai.agents.country_agent import ChatAnthropicClient, CountryAgent
from wargame_ai.agents.leader_profile import LeaderProfileError, parse_persona_file
from wargame_ai.memory.embeddings import Embedder, HashEmbedder, VoyageEmbedder
from wargame_ai.memory.store import AgentMemoryStore
from wargame_ai.sim.extractors import default_extractors
from wargame_ai.sim.loop import ScenarioSpec, SimLoop, seed_world_from_countries
from wargame_ai.sim.signals import SignalCollector
from wargame_ai.sim.world import WorldState

log = structlog.get_logger(__name__)

_SEEDS_DIR = Path(__file__).resolve().parents[2] / "wargame_shared" / "seeds"
_COUNTRIES_YAML = _SEEDS_DIR / "countries.yaml"


class LangGraphSimRunner:
    """Production SimRunner — spawns a LangGraph-driven turn loop per sim.

    Satisfies :class:`app.sim_runner.SimRunner` (the runtime-checkable Protocol
    declared by the backend).
    """

    def __init__(self, redis_client: Any) -> None:
        self._redis = redis_client
        self._tasks: dict[uuid.UUID, asyncio.Task[None]] = {}
        self._loops: dict[uuid.UUID, SimLoop] = {}
        self._statuses: dict[uuid.UUID, SimulationStatus] = {}

    # ------------------------------------------------------------------ #
    # SimRunner Protocol                                                   #
    # ------------------------------------------------------------------ #

    async def start(self, simulation_id: uuid.UUID, scenario_id: uuid.UUID) -> None:
        """Launch the LangGraph sim loop in a background task."""
        log.info(
            "langgraph_runner_start",
            simulation_id=str(simulation_id),
            scenario_id=str(scenario_id),
        )
        self._statuses[simulation_id] = SimulationStatus.running
        task = asyncio.create_task(
            self._run_background(simulation_id, scenario_id),
            name=f"langgraph-sim-{simulation_id}",
        )
        self._tasks[simulation_id] = task
        task.add_done_callback(lambda _: self._cleanup(simulation_id))

    async def abort(self, simulation_id: uuid.UUID) -> None:
        """Signal the running loop to abort."""
        log.info("langgraph_runner_abort", simulation_id=str(simulation_id))
        loop = self._loops.get(simulation_id)
        if loop is not None:
            loop.abort()
        task = self._tasks.get(simulation_id)
        if task is not None and not task.done():
            # Give the loop a chance to flush a sim_complete frame before
            # we cancel the task as a hard stop.
            try:
                await asyncio.wait_for(task, timeout=5.0)
            except asyncio.TimeoutError:
                task.cancel()
        self._statuses[simulation_id] = SimulationStatus.aborted

    # ------------------------------------------------------------------ #
    # Internal                                                             #
    # ------------------------------------------------------------------ #

    def _cleanup(self, simulation_id: uuid.UUID) -> None:
        self._tasks.pop(simulation_id, None)
        self._loops.pop(simulation_id, None)

    async def _run_background(
        self,
        simulation_id: uuid.UUID,
        scenario_id: uuid.UUID,
    ) -> None:
        """Full per-simulation lifecycle: build world, run loop, mark completed."""
        settings = get_settings()

        try:
            async with AsyncSessionLocal() as db:
                # Load scenario from Postgres
                scenario_row = await db.get(ScenarioORM, scenario_id)
                if scenario_row is None:
                    log.error("scenario_not_found", scenario_id=str(scenario_id))
                    self._statuses[simulation_id] = SimulationStatus.error
                    return

                sim_row = await db.get(SimulationORM, simulation_id)
                if sim_row is None:
                    log.error("simulation_not_found", simulation_id=str(simulation_id))
                    return
                sim_row.status = SimulationStatus.running
                await db.flush()

                # Build world from seed YAML filtered by scenario.country_ids
                country_codes = [str(c).upper() for c in (scenario_row.country_ids or [])]
                world = self._build_world(country_codes)

                # Build agents + arbiter + memory store
                embedder = self._build_embedder(settings)
                memory_store = AgentMemoryStore(db, embedder)
                agents = self._build_agents(world, settings)
                arbiter = Arbiter(llm=None)  # LLM client injection pending

                # Seed each country's memory from recent data-lake events
                for code in country_codes:
                    try:
                        await memory_store.seed_from_events(
                            sim_id=simulation_id,
                            country_code=code,
                            lookback_days=60,
                            limit=50,
                        )
                    except Exception as exc:  # noqa: BLE001
                        log.warning("seed_memory_failed", code=code, error=str(exc))

                scenario = ScenarioSpec(
                    id=scenario_id,
                    title=scenario_row.title,
                    description=scenario_row.description,
                    country_codes=country_codes,
                    initial_conditions=dict(scenario_row.initial_conditions or {}),
                    max_turns=sim_row.max_turns or settings.max_turns,
                )

                signal_collector = SignalCollector(default_extractors())

                loop = SimLoop(
                    agents=agents,
                    arbiter=arbiter,
                    world=world,
                    memory_store=memory_store,
                    max_turns=scenario.max_turns,
                    signal_collector=signal_collector,
                )
                self._loops[simulation_id] = loop

                await loop.run(simulation_id, scenario, db, self._redis)

                # Mark as completed
                sim_row.status = (
                    SimulationStatus.aborted
                    if self._statuses.get(simulation_id) is SimulationStatus.aborted
                    else SimulationStatus.completed
                )
                sim_row.world_state_snapshot = world.snapshot()
                await db.commit()

                self._statuses[simulation_id] = sim_row.status

        except asyncio.CancelledError:
            log.info("langgraph_runner_cancelled", simulation_id=str(simulation_id))
            self._statuses[simulation_id] = SimulationStatus.aborted
            raise
        except Exception as exc:  # noqa: BLE001
            log.exception("langgraph_runner_error", simulation_id=str(simulation_id), error=str(exc))
            self._statuses[simulation_id] = SimulationStatus.error

    # ------------------------------------------------------------------ #
    # Builders                                                             #
    # ------------------------------------------------------------------ #

    def _build_world(self, country_codes: list[str]) -> WorldState:
        """Load the seed YAML and construct the initial world state."""
        seeds = self._load_country_seeds()
        filtered = [s for s in seeds if str(s["iso3"]).upper() in country_codes]
        return seed_world_from_countries(filtered)

    def _build_agents(
        self,
        world: WorldState,
        settings: Any,
    ) -> dict[str, CountryAgent]:
        """Instantiate one CountryAgent per country in the world."""
        agents: dict[str, CountryAgent] = {}
        llm_client: Any
        if settings.anthropic_api_key:
            llm_client = ChatAnthropicClient(
                model=settings.agent_model,
                api_key=settings.anthropic_api_key,
            )
        else:
            # Fall back to a simple "no_action" agent if no key is configured.
            log.warning("anthropic_api_key_missing_using_stub_agents")
            llm_client = _StubLLMClient()

        for code, state in world.countries.items():
            agents[code] = CountryAgent(
                code=code,
                name=state.name,
                doctrine=state.doctrine,
                red_lines=[r.description for r in state.red_lines],
                llm=llm_client,
                persona=state.persona,
            )
        return agents

    def _build_embedder(self, settings: Any) -> Embedder:
        """Return a production embedder or fall back to HashEmbedder."""
        if settings.voyage_api_key:
            return VoyageEmbedder(
                api_key=settings.voyage_api_key,
                model=settings.embedding_model,
                dimensions=settings.embedding_dims,
            )
        log.warning("voyage_api_key_missing_using_hash_embedder")
        return HashEmbedder(dimensions=settings.embedding_dims)

    @staticmethod
    def _load_country_seeds() -> list[dict[str, Any]]:
        if not _COUNTRIES_YAML.exists():
            log.error("countries_yaml_missing", path=str(_COUNTRIES_YAML))
            return []
        with _COUNTRIES_YAML.open("r", encoding="utf-8") as fp:
            data = yaml.safe_load(fp)
        countries = data.get("countries") if isinstance(data, dict) else data
        records = list(countries or [])
        # Resolve persona_file references inline so downstream consumers (the
        # WorldState builder, tests) see a plain "persona" string field on each
        # record. Missing files degrade to an empty persona rather than failing
        # the whole sim.
        for record in records:
            if not isinstance(record, dict):
                continue
            ref = record.get("persona_file")
            if not isinstance(ref, str) or not ref.strip():
                continue
            persona_path = (_SEEDS_DIR / ref).resolve()
            try:
                raw_text = persona_path.read_text(encoding="utf-8")
            except OSError as exc:
                log.warning(
                    "persona_file_missing",
                    iso3=record.get("iso3"),
                    path=str(persona_path),
                    error=str(exc),
                )
                continue

            # Split YAML frontmatter (LeaderProfile + OCEAN) from the body.
            # Malformed frontmatter is logged but does not abort the sim — the
            # country falls back to a body-only persona with no structured
            # OCEAN block, matching how missing personas already degrade.
            try:
                profile, body = parse_persona_file(raw_text)
            except LeaderProfileError as exc:
                log.warning(
                    "persona_frontmatter_invalid",
                    iso3=record.get("iso3"),
                    path=str(persona_path),
                    error=str(exc),
                )
                profile, body = None, raw_text

            record["persona"] = body
            if profile is not None:
                record["leader_profile"] = profile
        return records


# ---------------------------------------------------------------------------
# Stub fallback                                                              #
# ---------------------------------------------------------------------------


class _StubLLMClient:
    """Emits an empty response so the agent falls back to ``no_action``."""

    async def ainvoke_tools(
        self,
        system_prompt: str,
        human_prompt: str,
        tools: list[dict[str, Any]],
    ) -> Any:
        from wargame_ai.agents.country_agent import LLMResponse

        return LLMResponse(tool_calls=[], content="stub client")
