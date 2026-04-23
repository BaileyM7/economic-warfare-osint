"""Simulations API — create, inspect, and abort simulation runs.

Endpoints:
  POST   /api/simulations           Create + enqueue a simulation
  GET    /api/simulations/{id}      Fetch current simulation state
  POST   /api/simulations/{id}/abort   Abort a running simulation
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from wargame_backend.app.config import Settings, get_settings
from wargame_backend.app.db.models import Scenario, Simulation, SimulationStatus
from wargame_backend.app.deps import get_db, get_sim_runner
from wargame_backend.app.rate_limit import limiter
from wargame_backend.app.sim_runner import SimRunner

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/api/simulations", tags=["simulations"])


# ---------------------------------------------------------------------------
# Request / response schemas
# ---------------------------------------------------------------------------


class SimulationCreate(BaseModel):
    """Request body for POST /api/simulations."""

    model_config = ConfigDict(str_strip_whitespace=True)

    scenario_id: uuid.UUID = Field(..., description="UUID of the scenario to run.")
    max_turns: int = Field(
        default=20,
        ge=1,
        le=100,
        description="Maximum number of turns.  Defaults to 20.",
    )
    config: dict[str, Any] = Field(
        default_factory=dict,
        description="Optional engine configuration overrides (model, random_seed, etc.).",
    )


class SimulationResponse(BaseModel):
    """Full simulation state returned by the API."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    scenario_id: str
    status: str
    current_turn: int
    max_turns: int
    world_state_snapshot: dict[str, Any] | None = None
    config: dict[str, Any] = Field(default_factory=dict)
    created_at: str
    started_at: str | None = None
    completed_at: str | None = None
    ws_url: str | None = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_ACTIVE_STATUSES = {SimulationStatus.pending, SimulationStatus.running, SimulationStatus.paused}


def _sim_to_response(sim: Simulation, ws_url: str | None = None) -> SimulationResponse:
    return SimulationResponse(
        id=str(sim.id),
        scenario_id=str(sim.scenario_id),
        status=sim.status.value,
        current_turn=sim.current_turn,
        max_turns=sim.max_turns,
        world_state_snapshot=sim.world_state_snapshot,
        config=sim.config,
        created_at=sim.created_at.isoformat(),
        started_at=sim.started_at.isoformat() if sim.started_at else None,
        completed_at=sim.completed_at.isoformat() if sim.completed_at else None,
        ws_url=ws_url,
    )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post("", status_code=202, response_model=dict[str, Any])
# Demo-friendly: 5/min made Execute clicks during iteration cycles return
# 429 before the runner even saw them.  The sim itself is the real capacity
# bottleneck (Anthropic TPM + ``max_concurrent_sims`` config), not this
# endpoint.  Bumped to 30/min so rapid preset-click cycles don't bounce.
@limiter.limit("30/minute")
async def create_simulation(
    request: Request,
    body: SimulationCreate,
    db: AsyncSession = Depends(get_db),
    sim_runner: SimRunner = Depends(get_sim_runner),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    """Create a simulation row and immediately start the sim runner.

    Returns HTTP 202 because the engine runs asynchronously; the caller
    should connect to the returned ``ws_url`` to receive events.

    Raises:
        404: Scenario not found.
        409: Scenario already has an active (pending/running/paused) simulation.
        429: Global concurrent simulation limit reached.
    """
    # -- Validate scenario exists --
    scenario_result = await db.execute(
        select(Scenario).where(Scenario.id == body.scenario_id)
    )
    scenario = scenario_result.scalar_one_or_none()
    if scenario is None:
        raise HTTPException(
            status_code=404,
            detail=f"Scenario '{body.scenario_id}' not found.",
        )

    # -- Check for an already-active sim on this scenario --
    active_result = await db.execute(
        select(Simulation).where(
            Simulation.scenario_id == body.scenario_id,
            Simulation.status.in_([s.value for s in _ACTIVE_STATUSES]),
        )
    )
    existing = active_result.scalar_one_or_none()
    if existing:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Scenario '{body.scenario_id}' already has an active simulation "
                f"(id={existing.id}, status={existing.status.value})."
            ),
        )

    # -- Check global concurrency limit --
    active_count_result = await db.execute(
        select(Simulation).where(
            Simulation.status.in_([s.value for s in _ACTIVE_STATUSES])
        )
    )
    active_count = len(active_count_result.scalars().all())
    if active_count >= settings.max_concurrent_sims:
        raise HTTPException(
            status_code=429,
            detail=(
                f"Maximum concurrent simulations ({settings.max_concurrent_sims}) reached. "
                "Wait for an existing simulation to complete."
            ),
        )

    # -- Create simulation row --
    simulation = Simulation(
        scenario_id=body.scenario_id,
        status=SimulationStatus.pending,
        current_turn=0,
        max_turns=body.max_turns,
        config=body.config,
    )
    db.add(simulation)
    await db.flush()
    await db.refresh(simulation)

    # -- Kick off the runner (non-blocking) --
    await sim_runner.start(simulation.id, body.scenario_id)

    ws_url = f"/ws/simulations/{simulation.id}"
    log.info(
        "Simulation created and started",
        simulation_id=str(simulation.id),
        scenario_id=str(body.scenario_id),
    )

    return {
        "data": _sim_to_response(simulation, ws_url=ws_url).model_dump(mode="json"),
        "error": None,
    }


@router.get("/{simulation_id}", response_model=dict[str, Any])
async def get_simulation(
    simulation_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Return the current state of a simulation."""
    result = await db.execute(
        select(Simulation).where(Simulation.id == simulation_id)
    )
    sim = result.scalar_one_or_none()
    if sim is None:
        raise HTTPException(
            status_code=404, detail=f"Simulation '{simulation_id}' not found."
        )

    ws_url = f"/ws/simulations/{sim.id}" if sim.status in _ACTIVE_STATUSES else None
    return {
        "data": _sim_to_response(sim, ws_url=ws_url).model_dump(mode="json"),
        "error": None,
    }


@router.post("/{simulation_id}/abort", response_model=dict[str, Any])
async def abort_simulation(
    simulation_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    sim_runner: SimRunner = Depends(get_sim_runner),
) -> dict[str, Any]:
    """Abort a running or paused simulation.

    Raises:
        404: Simulation not found.
        409: Simulation is not in an abortable state.
    """
    result = await db.execute(
        select(Simulation).where(Simulation.id == simulation_id)
    )
    sim = result.scalar_one_or_none()
    if sim is None:
        raise HTTPException(
            status_code=404, detail=f"Simulation '{simulation_id}' not found."
        )
    if sim.status not in _ACTIVE_STATUSES:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Simulation '{simulation_id}' cannot be aborted "
                f"(current status: {sim.status.value})."
            ),
        )

    await sim_runner.abort(simulation_id)

    # Update DB row
    sim.status = SimulationStatus.aborted
    await db.flush()

    log.info("Simulation aborted", simulation_id=str(simulation_id))
    return {
        "data": _sim_to_response(sim).model_dump(mode="json"),
        "error": None,
    }
