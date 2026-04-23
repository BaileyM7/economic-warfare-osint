"""SimLoop — the turn-by-turn orchestrator.

Builds a LangGraph ``StateGraph`` with nodes:

    scenario_loader → fan_out → [country_agents in parallel] → arbiter
                                 → world_updater → event_emitter
                                 → termination_check → (loop | END)

Design notes:
  * Each turn is one full traversal of the graph body.  The graph's
    ``termination_check`` node decides whether to loop.  We express the loop
    as LangGraph conditional edges so the async execution remains fully
    event-driven.
  * State is a plain ``SimLoopState`` TypedDict so LangGraph's state merging
    does not fight us.  The heavy objects (``WorldState``, LLM clients) live
    on the ``SimLoop`` instance; state only carries per-turn payloads.
  * Control channel (``sim:{id}:control``) is polled from a background task
    that sets an asyncio.Event when pause / abort arrives.
"""

from __future__ import annotations

import asyncio
import json
import os
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, TypedDict

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from wargame_shared.schemas.sim_event import (
    Citation,
    Domain,
    EscalationRung,
    Explainability,
    FactorKind,
    SimEvent,
    TriggeringFactor,
)

from wargame_ai.agents.arbiter import Arbiter
from wargame_ai.agents.country_agent import CountryAgent, MemoryRecord, Perception
from wargame_ai.sim.escalation_ladder import classify_action
from wargame_ai.sim.world import (
    CountryState,
    ProposedAction,
    RedLine,
    Relationship,
    ResolvedAction,
    ResolvedOutcome,
    WorldState,
)

try:  # pragma: no cover — optional import
    import redis.asyncio as aioredis
except ImportError:  # pragma: no cover
    aioredis = None  # type: ignore[assignment]


log = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Scenario DTO
# ---------------------------------------------------------------------------


@dataclass
class ScenarioSpec:
    """Minimal scenario description consumed by ``SimLoop``.

    The backend persists this as a ``scenarios`` row; here we use a plain
    dataclass so the sim engine stays decoupled from the ORM.
    """

    id: uuid.UUID
    title: str
    description: str
    country_codes: list[str]
    initial_conditions: dict[str, Any]
    max_turns: int = 20


# ---------------------------------------------------------------------------
# LangGraph state
# ---------------------------------------------------------------------------


class SimLoopState(TypedDict, total=False):
    """State threaded through LangGraph nodes on each turn.

    Heavy objects (world, agents) live on the ``SimLoop`` instance; state
    carries only per-turn payloads to keep serialisation cheap.
    """

    turn: int
    proposed: list[ProposedAction]
    resolved: list[ResolvedAction]
    emitted_events: list[SimEvent]
    terminate: bool


# ---------------------------------------------------------------------------
# Channel naming — mirrors backend.app.sim_runner
# ---------------------------------------------------------------------------


def events_channel(sim_id: uuid.UUID) -> str:
    """Redis PubSub channel for simulation events."""
    return f"sim:{sim_id}:events"


def control_channel(sim_id: uuid.UUID) -> str:
    """Redis PubSub channel for simulation control messages."""
    return f"sim:{sim_id}:control"


# ---------------------------------------------------------------------------
# SimLoop
# ---------------------------------------------------------------------------


class SimLoop:
    """Full turn loop wiring: LangGraph + agents + arbiter + world + DB/Redis.

    Usage:
        loop = SimLoop(agents, arbiter, world, memory_store=None)
        await loop.run(simulation_id, scenario, db, redis)
    """

    def __init__(
        self,
        agents: dict[str, CountryAgent],
        arbiter: Arbiter,
        world: WorldState,
        memory_store: Any | None = None,
        max_turns: int = 20,
        signal_collector: Any | None = None,
    ) -> None:
        self.agents = agents
        self.arbiter = arbiter
        self.world = world
        self.memory_store = memory_store
        self.max_turns = max_turns
        # Optional SignalCollector — when provided AND a DB session is in
        # scope during run(), each agent's perception is enriched with a
        # short "## Recent intelligence" block compiled from the data lake.
        # Keeping this optional means tests and stub-only runs continue to
        # work without a Postgres connection.
        self.signal_collector = signal_collector
        self._db_session: AsyncSession | None = None

        self._paused = asyncio.Event()
        self._aborted = asyncio.Event()
        self._seq = 0  # monotonic WS frame counter
        self._peak_rung = 0
        self._total_events = 0

    # ------------------------------------------------------------------ #
    # Public runner                                                        #
    # ------------------------------------------------------------------ #

    async def run(
        self,
        simulation_id: uuid.UUID,
        scenario: ScenarioSpec,
        db: AsyncSession | None,
        redis: Any | None,
    ) -> None:
        """Execute the full turn loop for one simulation.

        Args:
            simulation_id: The owning simulation UUID (used for Redis channels
                and DB persistence).
            scenario: Scenario spec controlling turn count and initial
                conditions.
            db: Async SQLAlchemy session (or ``None`` to skip persistence,
                useful in tests).
            redis: Async Redis client (or ``None`` for no PubSub, tests).
        """
        log.info(
            "sim_loop_start",
            simulation_id=str(simulation_id),
            countries=list(self.agents.keys()),
            max_turns=scenario.max_turns,
        )

        # Start control-channel listener (pause / resume / abort)
        control_task: asyncio.Task[None] | None = None
        if redis is not None:
            control_task = asyncio.create_task(
                self._listen_control(simulation_id, redis),
                name=f"sim-control-{simulation_id}",
            )

        # Stash the session for the duration of the run so per-turn helpers
        # (e.g. _gather_proposals → SignalCollector) can issue queries
        # without rethreading the parameter through every method.
        self._db_session = db

        try:
            # Apply initial perturbation, if any, as turn 0 seed events
            await self._apply_seed_events(simulation_id, scenario, db, redis)

            turn = 1
            while turn <= min(scenario.max_turns, self.max_turns):
                if self._aborted.is_set():
                    log.info("sim_loop_aborted", turn=turn)
                    break

                # Respect pause
                while self._paused.is_set() and not self._aborted.is_set():
                    await asyncio.sleep(0.1)
                if self._aborted.is_set():
                    break

                self.world.turn = turn
                await self._emit_turn_start(simulation_id, redis)

                # --- Parallel agent perceive → decide → propose ----------
                proposed = await self._gather_proposals()

                # Pace before the arbiter's LLM call. Honor AGENT_PACE_SECONDS
                # so Tier-1 users serialize; Tier 2+ with pace=0 skips the wait.
                arbiter_pace = float(os.environ.get("AGENT_PACE_SECONDS", "0"))
                if arbiter_pace > 0:
                    await asyncio.sleep(arbiter_pace)

                # --- Arbiter resolves ----------------------------------
                resolved = await self.arbiter.resolve(proposed, self.world)

                # --- Apply to world state + emit + persist -------------
                events_this_turn: list[SimEvent] = []
                for action in resolved:
                    self.world.apply(action)
                    if action.outcome is ResolvedOutcome.accepted:
                        event = self._build_sim_event(simulation_id, action)
                        events_this_turn.append(event)
                        self.world.record_event(event)
                        await self._persist_sim_event(db, event, action)
                        await self._emit_sim_event(simulation_id, redis, event)
                        self._total_events += 1
                        self._peak_rung = max(self._peak_rung, event.escalation_rung)

                # --- Remember decisions in memory store ----------------
                await self._remember_turn(simulation_id, resolved)

                # --- Turn end ------------------------------------------
                await self._emit_turn_end(simulation_id, redis, events_this_turn, resolved)

                if self._terminal_reached(events_this_turn):
                    log.info("sim_loop_terminal_state", turn=turn)
                    break

                turn += 1

            await self._emit_sim_complete(
                simulation_id,
                redis,
                status="aborted" if self._aborted.is_set() else "completed",
            )

        finally:
            if control_task is not None:
                control_task.cancel()
                try:
                    await control_task
                except (asyncio.CancelledError, Exception):  # noqa: BLE001
                    pass

    # ------------------------------------------------------------------ #
    # Control channel                                                      #
    # ------------------------------------------------------------------ #

    def pause(self) -> None:
        """Signal the loop to pause after the current turn."""
        self._paused.set()

    def resume(self) -> None:
        """Resume a paused loop."""
        self._paused.clear()

    def abort(self) -> None:
        """Signal the loop to stop immediately after the current turn."""
        self._aborted.set()
        self._paused.clear()

    async def _listen_control(self, sim_id: uuid.UUID, redis: Any) -> None:
        """Subscribe to the control channel and mutate pause / abort flags."""
        pubsub = redis.pubsub()
        channel = control_channel(sim_id)
        try:
            await pubsub.subscribe(channel)
            async for message in pubsub.listen():
                if message.get("type") != "message":
                    continue
                data = message.get("data")
                if isinstance(data, bytes):
                    data = data.decode("utf-8")
                try:
                    parsed = json.loads(data)
                except (TypeError, json.JSONDecodeError):
                    continue
                action = (parsed or {}).get("action")
                log.info("sim_loop_control", action=action)
                if action == "pause":
                    self.pause()
                elif action == "resume":
                    self.resume()
                elif action == "abort":
                    self.abort()
                    break
        except asyncio.CancelledError:
            pass
        finally:
            try:
                await pubsub.unsubscribe(channel)
                await pubsub.aclose()  # redis-py >= 5 removed close(); use aclose()
            except Exception:  # noqa: BLE001
                pass

    # ------------------------------------------------------------------ #
    # Parallel agent fan-out                                                #
    # ------------------------------------------------------------------ #

    async def _gather_proposals(self) -> list[ProposedAction]:
        """Run every country agent in parallel and collect ProposedActions."""

        async def run_one(code: str, agent: CountryAgent) -> ProposedAction:
            country_state = self.world.countries[code]
            memories = await self._recall_memories(code, country_state)
            recent_signals: list[Any] = []
            if self.signal_collector is not None and self._db_session is not None:
                try:
                    recent_signals = await self.signal_collector.collect_for(
                        self._db_session, code
                    )
                except Exception as exc:  # noqa: BLE001
                    log.warning(
                        "signal_collector_failed",
                        actor=code,
                        error=str(exc),
                    )
            perception = Perception(
                country_iso3=code,
                country_name=country_state.name,
                doctrine=country_state.doctrine,
                red_lines=[r.description for r in country_state.red_lines],
                current_posture={k: v.value for k, v in country_state.posture.items()},
                resource_budget=country_state.resource_budget.model_dump(),
                world_view=self.world.summarize_for(code),
                memories=memories,
                persona=country_state.persona,
                leader_profile=country_state.leader_profile,
                recent_signals=recent_signals,
                consecutive_no_action_turns=country_state.consecutive_no_action_turns,
                recent_domains=list(country_state.recent_domains),
            )
            try:
                return await agent.act(perception, memories)
            except Exception as exc:  # noqa: BLE001
                log.error("agent_act_failed", actor=code, error=str(exc))
                return ProposedAction(
                    actor=code,
                    target=None,
                    domain=Domain.diplomatic,
                    action_type="no_action",
                    payload={"reason": f"agent error: {exc}"},
                    rationale="Agent raised an exception; defaulting to no_action.",
                    estimated_escalation_rung=0,
                )

        # Agent fan-out strategy keyed off AGENT_PACE_SECONDS:
        #   0   → parallel via asyncio.gather (requires Tier 2+ on Anthropic:
        #         50 RPM + 50k ITPM comfortably fits 10 agents/turn).
        #   >0  → sequential with that many seconds between calls (Tier 1 mode,
        #         e.g. 13 s to stay under 5 RPM).
        pace = float(os.environ.get("AGENT_PACE_SECONDS", "0"))
        if pace <= 0:
            tasks = [run_one(code, agent) for code, agent in self.agents.items()]
            return list(await asyncio.gather(*tasks))
        proposals: list[ProposedAction] = []
        for i, (code, agent) in enumerate(self.agents.items()):
            if i > 0:
                log.debug("agent_pace_sleep", seconds=pace)
                await asyncio.sleep(pace)
            proposals.append(await run_one(code, agent))
        return proposals

    async def _recall_memories(
        self,
        country_code: str,
        country_state: CountryState,
    ) -> list[MemoryRecord]:
        """Retrieve top-k memories.  Returns [] if no memory store is wired."""
        if self.memory_store is None:
            return []
        try:
            query = f"turn {self.world.turn} context for {country_state.name}"
            records = await self.memory_store.recall(
                sim_id=self._current_sim_id,
                country_code=country_code,
                query=query,
                k=5,
            )
            return [
                MemoryRecord(
                    content=r.content,
                    memory_type=r.memory_type,
                    turn=r.turn,
                    score=r.score,
                )
                for r in records
            ]
        except Exception as exc:  # noqa: BLE001
            log.warning("memory_recall_failed", actor=country_code, error=str(exc))
            return []

    async def _remember_turn(
        self,
        sim_id: uuid.UUID,
        resolved: list[ResolvedAction],
    ) -> None:
        """Store each accepted action as a decision memory for the actor."""
        if self.memory_store is None:
            return
        for action in resolved:
            if action.outcome is not ResolvedOutcome.accepted:
                continue
            content = (
                f"Turn {self.world.turn}: I chose '{action.proposed.action_type}' "
                f"against {action.proposed.target or '[none]'} "
                f"(rung={action.final_escalation_rung}). "
                f"Rationale: {action.proposed.rationale}"
            )
            try:
                # KNOWN COMPROMISE: ai.* imports from app.db.models for MemoryType.
                # The correct long-term fix is dependency injection or a shared types module.
                # Accepted for the prototype; see docs/architecture.md "Known compromises".
                from wargame_backend.app.db.models import MemoryType

                await self.memory_store.remember(
                    sim_id=sim_id,
                    country_code=action.proposed.actor,
                    content=content,
                    memory_type=MemoryType.decision,
                    turn=self.world.turn,
                    metadata={"action_type": action.proposed.action_type},
                )
            except Exception as exc:  # noqa: BLE001
                log.warning("memory_remember_failed", actor=action.proposed.actor, error=str(exc))

    # ------------------------------------------------------------------ #
    # Event building & persistence                                         #
    # ------------------------------------------------------------------ #

    _current_sim_id: uuid.UUID | None = None

    def _verify_explainability(
        self,
        actor: str,
        explainability: Explainability | None,
    ) -> Explainability | None:
        """Soft-validate `kind=event` factor refs against the actor's perception.

        Factors whose `ref` cannot be matched to a known SimEvent in the actor's
        recent perception window are kept but flagged with ``verified=False`` so
        the UI can render them muted. We never reject the action — explainability
        is informative, not gating.
        """
        if explainability is None:
            return None

        # Build the set of event IDs the actor could have legitimately cited:
        # everything in recent_events where they were actor or target.
        visible_ids: set[str] = {
            str(e.id)
            for e in self.world.recent_events
            if e.actor_country == actor or e.target_country == actor
        }

        verified_factors: list[TriggeringFactor] = []
        for factor in explainability.triggering_factors:
            if factor.kind is FactorKind.event and factor.ref not in visible_ids:
                log.info(
                    "explainability_factor_unverified",
                    actor=actor,
                    kind=factor.kind.value,
                    ref=factor.ref,
                )
                verified_factors.append(factor.model_copy(update={"verified": False}))
            else:
                verified_factors.append(factor)

        return explainability.model_copy(update={"triggering_factors": verified_factors})

    def _build_sim_event(
        self,
        sim_id: uuid.UUID,
        action: ResolvedAction,
    ) -> SimEvent:
        """Assemble a full SimEvent from a ResolvedAction."""
        self._current_sim_id = sim_id
        p = action.proposed
        rung = action.final_escalation_rung or int(classify_action(p))
        rung = max(0, min(5, rung))

        # Citations: forward from payload if the agent provided any.
        citations: list[Citation] = []
        for raw in p.payload.get("citations", []) or []:
            if isinstance(raw, dict) and "source" in raw and "ref" in raw:
                try:
                    citations.append(Citation(source=str(raw["source"]), ref=str(raw["ref"])))
                except Exception:  # noqa: BLE001
                    pass

        explainability = self._verify_explainability(p.actor, p.explainability)

        return SimEvent(
            sim_id=sim_id,
            parent_event_id=None,
            turn=self.world.turn,
            actor_country=p.actor,
            target_country=p.target,
            domain=p.domain,
            action_type=p.action_type,
            payload=dict(p.payload),
            rationale=p.rationale,
            citations=citations,
            escalation_rung=int(EscalationRung(rung)),
            explainability=explainability,
        )

    async def _persist_sim_event(
        self,
        db: AsyncSession | None,
        event: SimEvent,
        action: ResolvedAction,
    ) -> None:
        """Persist a SimEvent to Postgres (best-effort — never crash the loop)."""
        if db is None:
            return
        try:
            # KNOWN COMPROMISE: ai.* imports from app.db.models for ORM types.
            # Accepted for the prototype; see docs/architecture.md "Known compromises".
            from wargame_backend.app.db.models import SimEvent as SimEventORM

            row = SimEventORM(
                id=event.id,
                sim_id=event.sim_id,
                parent_event_id=event.parent_event_id,
                turn=event.turn,
                actor_country=event.actor_country,
                target_country=event.target_country,
                domain=event.domain.value,
                action_type=event.action_type,
                payload=event.payload,
                rationale=event.rationale,
                citations=[c.model_dump() for c in event.citations],
                escalation_rung=event.escalation_rung,
                explainability=(
                    event.explainability.model_dump(mode="json")
                    if event.explainability is not None
                    else None
                ),
                timestamp=event.timestamp,
            )
            db.add(row)
            await db.flush()
        except Exception as exc:  # noqa: BLE001
            log.warning("persist_sim_event_failed", error=str(exc))

    # ------------------------------------------------------------------ #
    # Redis PubSub emission                                                #
    # ------------------------------------------------------------------ #

    def _next_seq(self) -> int:
        self._seq += 1
        return self._seq

    async def _publish(self, sim_id: uuid.UUID, redis: Any | None, frame: dict) -> None:
        """Best-effort JSON publish to the events channel."""
        if redis is None:
            return
        try:
            await redis.publish(events_channel(sim_id), json.dumps(frame, default=str))
        except Exception as exc:  # noqa: BLE001
            log.warning("redis_publish_failed", error=str(exc))

    async def _emit_turn_start(self, sim_id: uuid.UUID, redis: Any | None) -> None:
        await self._publish(
            sim_id,
            redis,
            {
                "frame_type": "turn_start",
                "sim_id": str(sim_id),
                "seq": self._next_seq(),
                "ts": _now_iso(),
                "payload": {
                    "frame_type": "turn_start",
                    "turn": self.world.turn,
                    "world_state": {
                        "relationships": {
                            f"{a}-{b}": {
                                "posture": _bucket_posture(rel),
                                "trust_score": rel.trust_score / 100.0,
                            }
                            for (a, b), rel in self.world.relationships.items()
                        },
                        "posture_map": {
                            code: c.posture.get("diplomatic").value
                            if c.posture.get("diplomatic") is not None
                            else "neutral"
                            for code, c in self.world.countries.items()
                        },
                    },
                },
            },
        )

    async def _emit_sim_event(
        self,
        sim_id: uuid.UUID,
        redis: Any | None,
        event: SimEvent,
    ) -> None:
        await self._publish(
            sim_id,
            redis,
            {
                "frame_type": "sim_event",
                "sim_id": str(sim_id),
                "seq": self._next_seq(),
                "ts": _now_iso(),
                "payload": {
                    "frame_type": "sim_event",
                    "event": event.model_dump(mode="json"),
                },
            },
        )

    async def _emit_turn_end(
        self,
        sim_id: uuid.UUID,
        redis: Any | None,
        events_this_turn: list[SimEvent],
        resolved: list[ResolvedAction],
    ) -> None:
        max_rung = max((e.escalation_rung for e in events_this_turn), default=0)
        rel_deltas: dict[str, dict[str, int]] = {}
        for action in resolved:
            if action.outcome is not ResolvedOutcome.accepted:
                continue
            if action.proposed.target is None:
                continue
            a, b = sorted((action.proposed.actor, action.proposed.target))
            key = f"{a}-{b}"
            rel_deltas.setdefault(key, {"trust_score_delta": 0})
            # Canonical range is -100..100 int (matches sim engine WorldState)
            rel_deltas[key]["trust_score_delta"] += action.trust_delta

        await self._publish(
            sim_id,
            redis,
            {
                "frame_type": "turn_end",
                "sim_id": str(sim_id),
                "seq": self._next_seq(),
                "ts": _now_iso(),
                "payload": {
                    "frame_type": "turn_end",
                    "turn": self.world.turn,
                    "events_count": len(events_this_turn),
                    "relationship_deltas": rel_deltas,
                    "max_escalation_rung_this_turn": max_rung,
                },
            },
        )

    async def _emit_sim_complete(
        self,
        sim_id: uuid.UUID,
        redis: Any | None,
        status: str,
    ) -> None:
        await self._publish(
            sim_id,
            redis,
            {
                "frame_type": "sim_complete",
                "sim_id": str(sim_id),
                "seq": self._next_seq(),
                "ts": _now_iso(),
                "payload": {
                    "frame_type": "sim_complete",
                    "status": status,
                    "total_turns": self.world.turn,
                    "total_events": self._total_events,
                    "final_world_state": self.world.snapshot(),
                    "peak_escalation_rung": self._peak_rung,
                    "outcome_summary": f"Simulation {status} after {self.world.turn} turns.",
                },
            },
        )

    # ------------------------------------------------------------------ #
    # Seed events / termination                                            #
    # ------------------------------------------------------------------ #

    async def _apply_seed_events(
        self,
        sim_id: uuid.UUID,
        scenario: ScenarioSpec,
        db: AsyncSession | None,
        redis: Any | None,
    ) -> None:
        """Emit any scenario-defined seed events at turn 0."""
        seeds = scenario.initial_conditions.get("seed_events") or []
        if not seeds:
            return
        self.world.turn = 0
        await self._emit_turn_start(sim_id, redis)
        for seed in seeds:
            try:
                # seed may be a SeedEvent Pydantic model (when validated via API)
                # or a raw dict (when constructed programmatically in tests).
                if hasattr(seed, "model_dump"):
                    seed_dict = seed.model_dump()
                else:
                    seed_dict = dict(seed)
                actor = str(seed_dict["actor_country"]).upper()
                target = seed_dict.get("target_country")
                domain = Domain(seed_dict.get("domain", "info"))
                seed_payload = dict(seed_dict.get("payload", {}))
                # Mark the payload so the UI can badge seed events distinctly
                seed_payload["_origin"] = "scenario_seed"
                action_type = str(seed_dict.get("action_type", "scenario_seed"))
                rationale = str(seed_dict.get("rationale", "Scenario seed event."))
                event = SimEvent(
                    sim_id=sim_id,
                    turn=0,
                    actor_country=actor,
                    target_country=target.upper() if target else None,
                    domain=domain,
                    action_type=action_type,
                    payload=seed_payload,
                    rationale=rationale,
                    citations=[],
                    escalation_rung=int(seed_dict.get("escalation_rung", 2)),
                    # Auto-synthesize an Explainability triplet for the seed so
                    # clicking the T0 arc opens the same "Action / Because /
                    # In hopes of" card that live events get.  Without this
                    # the UI falls back to a muted "Legacy rationale" box and
                    # the seed arcs look visually worse than agent-generated
                    # arcs — the exact visual gap between demo and live that
                    # users hit.
                    explainability=_synthesize_seed_explainability(
                        action_type=action_type,
                        rationale=rationale,
                    ),
                )
            except Exception as exc:  # noqa: BLE001
                log.warning("bad_seed_event", error=str(exc))
                continue

            self.world.record_event(event)
            await self._emit_sim_event(sim_id, redis, event)
            # Persist seed too (treated as a real sim_event)
            dummy_action = ResolvedAction(
                proposed=ProposedAction(
                    actor=event.actor_country,
                    target=event.target_country,
                    domain=event.domain,
                    action_type=event.action_type,
                    payload=dict(event.payload),
                    rationale=event.rationale,
                    estimated_escalation_rung=event.escalation_rung,
                ),
                outcome=ResolvedOutcome.accepted,
                final_escalation_rung=event.escalation_rung,
            )
            await self._persist_sim_event(db, event, dummy_action)
            self._total_events += 1
            self._peak_rung = max(self._peak_rung, event.escalation_rung)

        await self._emit_turn_end(sim_id, redis, [], [])

    def _terminal_reached(self, events_this_turn: list[SimEvent]) -> bool:
        """Return True if any event this turn hit general war."""
        return any(e.escalation_rung >= 5 for e in events_this_turn)


# ---------------------------------------------------------------------------
# Helpers / posture bucketing                                                #
# ---------------------------------------------------------------------------


def _synthesize_seed_explainability(
    *, action_type: str, rationale: str
) -> Explainability:
    """Build an ``Explainability`` for a scenario seed event.

    Seed events don't have an agent behind them — they're pre-authored by
    the scenario — so we can't extract a genuine triggering-factor chain.
    Instead we produce an honest minimal triplet that labels itself as
    scenario-provided, so the UI renders the full three-slot card (matching
    demo behavior) without pretending the seed was agent reasoning.
    """
    action_human = action_type.replace("_", " ")
    # Clamp to Explainability field limits: summary ≤160, intended_outcome ≤240.
    summary = f"Scenario-seeded {action_human} (inciting event)."[:160]
    intended_outcome = (
        rationale[:240]
        if rationale and rationale != "Scenario seed event."
        else (
            "Establish the opening-move state of the simulation so downstream "
            "agent responses have a concrete inciting event to react to."
        )
    )
    return Explainability(
        summary=summary,
        triggering_factors=[
            TriggeringFactor(
                kind=FactorKind.perception,
                ref="scenario.seed",
                note=(
                    "Pre-authored inciting event from the scenario preset — "
                    "not the product of agent reasoning."
                ),
                verified=True,
            )
        ],
        intended_outcome=intended_outcome,
    )


def _bucket_posture(rel: Relationship) -> str:
    """Map trust+hostility into the 5-bucket posture enum expected by the WS."""
    if rel.trust_score >= 60:
        return "allied"
    if rel.trust_score >= 20:
        return "friendly"
    if rel.hostility_index >= 60 or rel.trust_score <= -60:
        return "hostile"
    if rel.hostility_index >= 20 or rel.trust_score <= -20:
        return "tense"
    return "neutral"


def _now_iso() -> str:
    """ISO-8601 UTC timestamp."""
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Convenience builders                                                       #
# ---------------------------------------------------------------------------


def seed_world_from_countries(
    countries: list[dict[str, Any]],
) -> WorldState:
    """Build an initial :class:`WorldState` from a list of country seed dicts.

    Each seed dict must have keys: ``iso3``, ``name``, ``doctrine``,
    ``red_lines``.  Other fields are passed through as country profile.
    """
    state = WorldState()
    for c in countries:
        code = str(c["iso3"]).upper()
        red_lines = [
            RedLine(description=str(rl)) for rl in (c.get("red_lines") or [])
        ]
        state.countries[code] = CountryState(
            iso3=code,
            name=str(c.get("name", code)),
            red_lines=red_lines,
            doctrine=str(c.get("doctrine", "") or ""),
            persona=str(c.get("persona", "") or ""),
            leader_profile=c.get("leader_profile"),
        )
    # Pre-populate relationships at neutral baseline
    codes = list(state.countries.keys())
    for i, a in enumerate(codes):
        for b in codes[i + 1 :]:
            state.get_relationship(a, b)
    return state
