"""Scenarios API — CRUD for user-authored what-if scenarios.

Endpoints:
  POST   /api/scenarios                Create a new scenario
  GET    /api/scenarios                Paginated list
  GET    /api/scenarios/{id}           Single scenario by UUID
  POST   /api/scenarios/extract-events Extract structured SeedEvents from prose (STUB)
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from wargame_backend.app.db.models import Scenario, ScenarioStatus
from wargame_backend.app.deps import get_db
from wargame_backend.app.rate_limit import limiter
from wargame_shared.schemas.scenario import ScenarioCreate, ScenarioResponse, SeedEvent

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/api/scenarios", tags=["scenarios"])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _orm_to_response(scenario: Scenario) -> ScenarioResponse:
    """Map ORM row → Pydantic response schema."""
    return ScenarioResponse.model_validate(scenario)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post("", status_code=201, response_model=dict[str, Any])
@limiter.limit("30/minute")
async def create_scenario(
    request: Request,
    body: ScenarioCreate,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Create a new scenario and return its full representation."""
    scenario = Scenario(
        title=body.title,
        description=body.description,
        country_ids=body.country_ids,
        initial_conditions=body.initial_conditions.model_dump(),
        status=ScenarioStatus.ready,
    )
    db.add(scenario)
    await db.flush()   # get server-generated id / timestamps
    await db.refresh(scenario)

    log.info("Scenario created", scenario_id=str(scenario.id), title=scenario.title)
    return {"data": _orm_to_response(scenario).model_dump(mode="json"), "error": None}


@router.get("", response_model=dict[str, Any])
async def list_scenarios(
    status: Annotated[
        str | None,
        Query(description="Filter by status: draft, ready, or archived."),
    ] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Return a paginated list of scenarios, optionally filtered by status."""
    stmt = select(Scenario).order_by(Scenario.created_at.desc())

    if status:
        try:
            status_enum = ScenarioStatus(status)
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid status '{status}'. Must be one of: draft, ready, archived.",
            )
        stmt = stmt.where(Scenario.status == status_enum)

    count_stmt = select(func.count()).select_from(stmt.subquery())
    total: int = (await db.execute(count_stmt)).scalar_one()

    rows = (await db.execute(stmt.offset(offset).limit(limit))).scalars().all()

    return {
        "data": {
            "items": [_orm_to_response(s).model_dump(mode="json") for s in rows],
            "total": total,
            "limit": limit,
            "offset": offset,
        },
        "error": None,
    }


@router.get("/{scenario_id}", response_model=dict[str, Any])
async def get_scenario(
    scenario_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Fetch a single scenario by UUID."""
    result = await db.execute(select(Scenario).where(Scenario.id == scenario_id))
    scenario = result.scalar_one_or_none()
    if scenario is None:
        raise HTTPException(status_code=404, detail=f"Scenario '{scenario_id}' not found.")

    return {"data": _orm_to_response(scenario).model_dump(mode="json"), "error": None}


# ---------------------------------------------------------------------------
# Seed-event extraction (STUB)
# ---------------------------------------------------------------------------
#
# When a user types a custom scenario description, we want to seed the sim
# with 1–3 SimEvents that match the prose — otherwise the world starts flat
# and agents default to no_action.  The eventual implementation feeds the
# prose to a small Claude call (Haiku) with the SeedEvent schema as a
# structured-output spec, then returns the extracted events for the user
# to confirm / edit before launching the sim.
#
# This stub returns an empty list so the frontend can wire up the UI flow
# (button → loading state → confirmation dialog → edit → launch) today and
# swap in the real LLM call later without a contract change.


class ExtractEventsRequest(BaseModel):
    """Body for POST /api/scenarios/extract-events."""

    description: str = Field(
        min_length=1,
        max_length=4000,
        description="Free-text scenario description typed by the user.",
    )
    country_ids: list[str] = Field(
        default_factory=list,
        description=(
            "Optional hint — countries the user has in mind.  The LLM uses "
            "this to bias extraction toward events involving these actors."
        ),
    )


class ExtractEventsResponse(BaseModel):
    """Response body — the same shape InitialConditions expects."""

    seed_events: list[SeedEvent]
    posture_overrides: dict[str, str]
    is_stub: bool = Field(
        default=True,
        description=(
            "True while the endpoint is a stub returning placeholder data. "
            "When the real LLM-backed extraction lands this flips to False "
            "and the frontend can drop the 'preview only' disclaimer."
        ),
    )


@router.post("/extract-events", response_model=dict[str, Any])
@limiter.limit("10/minute")
async def extract_events(
    request: Request,
    body: ExtractEventsRequest,
) -> dict[str, Any]:
    """STUB — returns an empty seed-events list with ``is_stub=True``.

    The real implementation will call Claude Haiku with the SeedEvent
    schema as a tool, letting the model extract structured events from
    the user's prose.  Interface is locked now so the frontend UI flow
    can ship ahead of the LLM wiring.
    """
    log.info(
        "extract_events.stub_called",
        description_length=len(body.description),
        country_count=len(body.country_ids),
    )
    response = ExtractEventsResponse(
        seed_events=[],
        posture_overrides={},
        is_stub=True,
    )
    return {"data": response.model_dump(mode="json"), "error": None}
