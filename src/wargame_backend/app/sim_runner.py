"""SimRunner protocol and stub implementation.

Defines the interface that the Phase 4 AI engine must satisfy.
The `NullSimRunner` writes synthetic `SimEvent`s to Redis PubSub so the
API layer is fully testable before the real LangGraph engine lands.

Switch implementations via the `AGENT_RUNNER_IMPL` env var:
  - ``null``      → NullSimRunner (default; no AI required)
  - ``langgraph`` → real engine (imported lazily from ``ai.sim.runner``)
"""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Protocol, runtime_checkable

import structlog

from wargame_backend.app.db.models import SimulationStatus

if TYPE_CHECKING:
    import redis.asyncio as aioredis

log = structlog.get_logger(__name__)

# Redis channel naming helpers (must match the WS handler)
_EVENTS_CHANNEL = "sim:{sim_id}:events"
_CONTROL_CHANNEL = "sim:{sim_id}:control"


def events_channel(simulation_id: uuid.UUID) -> str:
    """Return the Redis PubSub channel name for simulation events."""
    return _EVENTS_CHANNEL.format(sim_id=simulation_id)


def control_channel(simulation_id: uuid.UUID) -> str:
    """Return the Redis PubSub channel name for simulation control messages."""
    return _CONTROL_CHANNEL.format(sim_id=simulation_id)


# ---------------------------------------------------------------------------
# Protocol (the interface Phase 4 must implement)
# ---------------------------------------------------------------------------


@runtime_checkable
class SimRunner(Protocol):
    """Contract between the API layer and the simulation engine.

    Phase 4 must provide an object that satisfies this protocol.
    Inject it via ``app.state.sim_runner`` at startup.
    """

    async def start(self, simulation_id: uuid.UUID, scenario_id: uuid.UUID) -> None:
        """Start a simulation run asynchronously.

        The implementation MUST:
          1. Mark the simulation row as ``running`` in Postgres.
          2. Begin publishing ``WsFrame``-compatible JSON to the Redis channel
             ``sim:{simulation_id}:events``.
          3. Return immediately (non-blocking); the actual run executes in the
             background (asyncio task or separate process).

        Args:
            simulation_id: UUID of the ``simulations`` row to execute.
            scenario_id: UUID of the source ``scenarios`` row.
        """
        ...

    async def abort(self, simulation_id: uuid.UUID) -> None:
        """Abort a running or paused simulation.

        The implementation MUST:
          1. Signal the running task to stop (e.g. publish to control channel).
          2. Mark the simulation row as ``aborted``.
          3. Publish a ``sim_complete`` frame with status ``aborted``.

        Args:
            simulation_id: UUID of the simulation to abort.
        """
        ...


# ---------------------------------------------------------------------------
# NullSimRunner — stub for development / testing
# ---------------------------------------------------------------------------


class NullSimRunner:
    """Stub implementation that publishes fake events to Redis PubSub.

    Does not call any LLM.  Useful for:
      - Local development without an Anthropic key
      - Unit and integration tests (inject ``fakeredis``)
      - Smoke-testing the WebSocket pipeline end-to-end

    Publishes the following event sequence:
      1. ``connected`` frame (already sent by WS handler on connect)
      2. ``turn_start`` (turn=0)
      3. Two ``sim_event`` frames (CHN→TWN, USA→CHN)
      4. ``turn_end`` (turn=0)
      5. ``sim_complete``
    """

    def __init__(self, redis_client: "aioredis.Redis") -> None:  # type: ignore[name-defined]
        self._redis = redis_client
        # Track running tasks so we can cancel on abort
        self._tasks: dict[uuid.UUID, asyncio.Task[None]] = {}
        self._statuses: dict[uuid.UUID, SimulationStatus] = {}

    async def start(self, simulation_id: uuid.UUID, scenario_id: uuid.UUID) -> None:
        """Schedule the fake event sequence in the background."""
        log.info(
            "NullSimRunner.start",
            simulation_id=str(simulation_id),
            scenario_id=str(scenario_id),
        )
        self._statuses[simulation_id] = SimulationStatus.running
        task = asyncio.create_task(
            self._run_fake_simulation(simulation_id),
            name=f"null-sim-{simulation_id}",
        )
        self._tasks[simulation_id] = task
        task.add_done_callback(lambda _: self._tasks.pop(simulation_id, None))

    async def abort(self, simulation_id: uuid.UUID) -> None:
        """Cancel the background task and publish sim_complete(aborted)."""
        log.info("NullSimRunner.abort", simulation_id=str(simulation_id))
        task = self._tasks.pop(simulation_id, None)
        if task and not task.done():
            task.cancel()
        self._statuses[simulation_id] = SimulationStatus.aborted
        await self._publish(
            simulation_id,
            {
                "frame_type": "sim_complete",
                "sim_id": str(simulation_id),
                "seq": 99,
                "ts": _now_iso(),
                "payload": {
                    "frame_type": "sim_complete",
                    "status": "aborted",
                    "total_turns": 0,
                    "total_events": 0,
                    "final_world_state": {},
                    "peak_escalation_rung": 0,
                    "outcome_summary": "Simulation aborted by user.",
                },
            },
        )

    # ------------------------------------------------------------------ #
    # Internal helpers                                                     #
    # ------------------------------------------------------------------ #

    async def _run_fake_simulation(self, simulation_id: uuid.UUID) -> None:
        """Publish a minimal event sequence to Redis."""
        channel = events_channel(simulation_id)
        sim_id_str = str(simulation_id)
        seq = 0

        try:
            await asyncio.sleep(0.3)  # brief pause so WS client can subscribe

            # turn_start (turn 0)
            seq += 1
            await self._publish(
                simulation_id,
                {
                    "frame_type": "turn_start",
                    "sim_id": sim_id_str,
                    "seq": seq,
                    "ts": _now_iso(),
                    "payload": {
                        "frame_type": "turn_start",
                        "turn": 0,
                        "world_state": {
                            "relationships": {
                                "CHN-TWN": {"posture": "hostile", "trust_score": -0.8},
                                "USA-JPN": {"posture": "allied", "trust_score": 0.9},
                            },
                            "posture_map": {"CHN": "aggressive", "USA": "deterrent"},
                        },
                    },
                },
            )
            log.debug("NullSimRunner published turn_start", channel=channel)
            await asyncio.sleep(0.5)

            # sim_event 1: CHN → TWN
            event_id_1 = str(uuid.uuid4())
            seq += 1
            await self._publish(
                simulation_id,
                {
                    "frame_type": "sim_event",
                    "sim_id": sim_id_str,
                    "seq": seq,
                    "ts": _now_iso(),
                    "payload": {
                        "frame_type": "sim_event",
                        "event": {
                            "id": event_id_1,
                            "sim_id": sim_id_str,
                            "parent_event_id": None,
                            "turn": 0,
                            "actor_country": "CHN",
                            "target_country": "TWN",
                            "domain": "kinetic_limited",
                            "action_type": "naval_blockade_declaration",
                            "payload": {
                                "assets_deployed": ["Type 055 destroyer"],
                                "area": "Taiwan Strait",
                            },
                            "rationale": "NullSimRunner: synthetic event for testing.",
                            "citations": [],
                            "escalation_rung": 3,
                            "timestamp": _now_iso(),
                        },
                    },
                },
            )
            log.debug("NullSimRunner published sim_event CHN→TWN", channel=channel)
            await asyncio.sleep(0.5)

            # sim_event 2: USA → CHN
            event_id_2 = str(uuid.uuid4())
            seq += 1
            await self._publish(
                simulation_id,
                {
                    "frame_type": "sim_event",
                    "sim_id": sim_id_str,
                    "seq": seq,
                    "ts": _now_iso(),
                    "payload": {
                        "frame_type": "sim_event",
                        "event": {
                            "id": event_id_2,
                            "sim_id": sim_id_str,
                            "parent_event_id": None,
                            "turn": 0,
                            "actor_country": "USA",
                            "target_country": "CHN",
                            "domain": "diplomatic",
                            "action_type": "formal_protest",
                            "payload": {"statement": "US condemns the blockade."},
                            "rationale": "NullSimRunner: synthetic event for testing.",
                            "citations": [],
                            "escalation_rung": 1,
                            "timestamp": _now_iso(),
                        },
                    },
                },
            )
            log.debug("NullSimRunner published sim_event USA→CHN", channel=channel)
            await asyncio.sleep(0.3)

            # turn_end
            seq += 1
            await self._publish(
                simulation_id,
                {
                    "frame_type": "turn_end",
                    "sim_id": sim_id_str,
                    "seq": seq,
                    "ts": _now_iso(),
                    "payload": {
                        "frame_type": "turn_end",
                        "turn": 0,
                        "events_count": 2,
                        "relationship_deltas": {
                            "CHN-TWN": {"trust_score_delta": -0.12},
                            "USA-CHN": {"trust_score_delta": -0.08},
                        },
                        "max_escalation_rung_this_turn": 3,
                    },
                },
            )
            log.debug("NullSimRunner published turn_end", channel=channel)
            await asyncio.sleep(0.3)

            # sim_complete
            seq += 1
            self._statuses[simulation_id] = SimulationStatus.completed
            await self._publish(
                simulation_id,
                {
                    "frame_type": "sim_complete",
                    "sim_id": sim_id_str,
                    "seq": seq,
                    "ts": _now_iso(),
                    "payload": {
                        "frame_type": "sim_complete",
                        "status": "completed",
                        "total_turns": 1,
                        "total_events": 2,
                        "final_world_state": {},
                        "peak_escalation_rung": 3,
                        "outcome_summary": "NullSimRunner: synthetic simulation complete.",
                    },
                },
            )
            log.info("NullSimRunner simulation complete", simulation_id=sim_id_str)

        except asyncio.CancelledError:
            log.info("NullSimRunner task cancelled", simulation_id=sim_id_str)
            raise

    async def _publish(self, simulation_id: uuid.UUID, frame: dict) -> None:  # type: ignore[type-arg]
        """Serialize a frame dict to JSON and publish to Redis."""
        channel = events_channel(simulation_id)
        await self._redis.publish(channel, json.dumps(frame))


def _now_iso() -> str:
    """Return the current UTC time as an ISO-8601 string."""
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def build_sim_runner(impl: str, redis_client: "aioredis.Redis") -> SimRunner:  # type: ignore[name-defined]
    """Instantiate the correct SimRunner based on the ``AGENT_RUNNER_IMPL`` setting.

    Args:
        impl: ``'null'`` or ``'langgraph'``.
        redis_client: Async Redis client.

    Returns:
        A ``SimRunner``-conforming object.

    Raises:
        ValueError: If ``impl`` is not a known implementation key.
    """
    if impl == "null":
        return NullSimRunner(redis_client)  # type: ignore[return-value]
    if impl == "langgraph":
        # Lazy import to avoid pulling in the entire AI stack at startup
        # when running with ``null`` runner (e.g. tests).
        try:
            from wargame_ai.sim.runner import LangGraphSimRunner  # type: ignore[import]

            return LangGraphSimRunner(redis_client)  # type: ignore[return-value]
        except ImportError as exc:
            raise ImportError(
                "AGENT_RUNNER_IMPL=langgraph requires the 'ai' package to be installed. "
                "Run 'uv sync' in the project root, or set AGENT_RUNNER_IMPL=null."
            ) from exc
    raise ValueError(
        f"Unknown AGENT_RUNNER_IMPL '{impl}'. Valid values: 'null', 'langgraph'."
    )
