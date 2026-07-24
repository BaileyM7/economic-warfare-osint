"""Scenarios API — CRUD for user-authored what-if scenarios.

Endpoints:
  POST   /api/scenarios                Create a new scenario
  GET    /api/scenarios                Paginated list
  GET    /api/scenarios/{id}           Single scenario by UUID
  POST   /api/scenarios/extract-events Extract structured SeedEvents from prose (Claude)
"""

from __future__ import annotations

import uuid
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Any, Literal

import structlog
import yaml
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from wargame_backend.app.config import get_settings
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
    await db.flush()  # get server-generated id / timestamps
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
# Seed-event extraction
# ---------------------------------------------------------------------------
#
# When a user types a custom scenario description, we seed the sim with 1–3
# SeedEvents that match the prose — otherwise the world starts flat and agents
# default to no_action.  The prose goes to a small Claude call (Haiku, see
# EXTRACT_MODEL) with a forced tool whose schema mirrors SeedEvent; the
# extracted events come back for the user to confirm / edit before launch.
# Any failure (no key, timeout, invalid output) degrades to the original
# empty-stub response with is_stub=True, so the endpoint never 500s.


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


class SelectedCountry(BaseModel):
    """One country chosen to participate in a free-form scenario.

    Mirrors the frontend `SelectedCountry` type so the confirmation card
    can render the rationale + relevance score next to each chip.
    """

    iso3: str
    relevance_score: float = 0.0
    rationale: str = ""


class ExtractEventsResponse(BaseModel):
    """Response body — the same shape InitialConditions expects."""

    seed_events: list[SeedEvent]
    selected_countries: list[SelectedCountry] = Field(default_factory=list)
    posture_overrides: dict[str, str]
    source: Literal["llm", "partial_fallback", "fallback"] = "fallback"
    is_stub: bool = Field(
        default=True,
        description=(
            "True when the response is the empty fallback (no LLM ran or it "
            "failed); the frontend shows the 'preview only' disclaimer. False "
            "when the events were extracted by the LLM."
        ),
    )


# Mirrors wargame_ai.sim.world.Posture — kept as a literal tuple so the backend
# doesn't import the wargame_ai/langchain graph just for six labels.
_POSTURES = ("cooperative", "neutral", "defensive", "deterrent", "aggressive", "belligerent")
# Mirrors wargame_shared.schemas.sim_event.Domain.
_DOMAINS = ("info", "diplomatic", "economic", "cyber", "kinetic_limited", "kinetic_general")

_SEEDS_DIR = Path(__file__).resolve().parents[3] / "wargame_shared" / "seeds"


@lru_cache(maxsize=1)
def _supported_countries() -> tuple[str, ...]:
    """ISO-3 codes the sim can actually run — the seeded countries."""
    try:
        data = yaml.safe_load((_SEEDS_DIR / "countries.yaml").read_text(encoding="utf-8"))
        countries = data.get("countries") if isinstance(data, dict) else data
        codes = tuple(
            str(c["iso3"]).upper() for c in countries or [] if isinstance(c, dict) and c.get("iso3")
        )
        if codes:
            return codes
    except Exception as exc:  # noqa: BLE001
        log.warning("countries_yaml_unreadable", error=str(exc))
    return ("CHN", "TWN", "USA", "JPN", "KOR", "PHL", "AUS", "PRK", "RUS", "IND")


def _extract_tool_schema() -> dict[str, Any]:
    codes = list(_supported_countries())
    return {
        "name": "record_scenario_setup",
        "description": (
            "Record the structured wargame setup extracted from the analyst's "
            "free-text scenario description."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "seed_events": {
                    "type": "array",
                    "description": (
                        "1-3 concrete inciting events that start the scenario at turn 0, "
                        "ordered by causality (the triggering act first)."
                    ),
                    "items": {
                        "type": "object",
                        "properties": {
                            "actor_country": {"type": "string", "enum": codes},
                            "target_country": {"type": ["string", "null"], "enum": codes + [None]},
                            "domain": {"type": "string", "enum": list(_DOMAINS)},
                            "action_type": {
                                "type": "string",
                                "description": "Short snake_case label, e.g. 'naval_blockade'.",
                            },
                            "rationale": {
                                "type": "string",
                                "description": "One sentence tying the event to the prose.",
                            },
                            "payload": {
                                "type": "object",
                                "description": "Domain-specific details (location, assets, scale).",
                            },
                            "escalation_rung": {
                                "type": "integer",
                                "minimum": 0,
                                "maximum": 5,
                                "description": (
                                    "0 peacetime, 1 gray zone, 2 coercive diplomacy, "
                                    "3 limited conflict, 4 regional war, 5 general war."
                                ),
                            },
                        },
                        "required": ["actor_country", "domain", "action_type", "rationale"],
                    },
                },
                "selected_countries": {
                    "type": "array",
                    "description": (
                        "2-6 participating countries, most relevant first. Always include "
                        "the aggressor and the primary target."
                    ),
                    "items": {
                        "type": "object",
                        "properties": {
                            "iso3": {"type": "string", "enum": codes},
                            "relevance_score": {"type": "number"},
                            "rationale": {"type": "string"},
                        },
                        "required": ["iso3", "relevance_score", "rationale"],
                    },
                },
                "posture_overrides": {
                    "type": "object",
                    "description": (
                        "ISO-3 -> starting posture, only for countries whose stance at "
                        "turn 0 clearly differs from neutral."
                    ),
                    "additionalProperties": {"type": "string", "enum": list(_POSTURES)},
                },
            },
            "required": ["seed_events", "selected_countries", "posture_overrides"],
        },
    }


_EXTRACT_SYSTEM = (
    "You are the scenario-setup extractor for a geopolitical wargame simulator. "
    "The analyst describes a hypothetical crisis in free text; you translate it into "
    "the structured setup the simulation engine needs. Extract only what the prose "
    "supports — do not invent escalations the analyst did not describe. Only the "
    "listed countries exist in the simulation; map other actors to the closest "
    "listed country or omit them. Record the setup with the tool."
)


@lru_cache(maxsize=1)
def _anthropic_client() -> Any:
    """Lazy singleton — imported here so the module loads without the SDK."""
    import anthropic

    settings = get_settings()
    return anthropic.AsyncAnthropic(
        api_key=settings.anthropic_api_key,
        timeout=25.0,
        max_retries=1,
    )


def _fallback_response(source: str = "fallback") -> ExtractEventsResponse:
    return ExtractEventsResponse(
        seed_events=[],
        selected_countries=[],
        posture_overrides={},
        source=source,  # type: ignore[arg-type]
        is_stub=True,
    )


async def _run_extraction(body: ExtractEventsRequest) -> ExtractEventsResponse:
    """One forced-tool Claude call, validated through the SeedEvent schema."""
    settings = get_settings()
    hint = ""
    if body.country_ids:
        hint = f"\n\nThe analyst pre-selected these countries: {', '.join(body.country_ids)}."
    response = await _anthropic_client().messages.create(
        model=settings.extract_model,
        max_tokens=2000,
        system=_EXTRACT_SYSTEM,
        messages=[
            {
                "role": "user",
                "content": f"Scenario description:\n\n{body.description}{hint}",
            }
        ],
        tools=[_extract_tool_schema()],
        tool_choice={"type": "tool", "name": "record_scenario_setup"},
    )
    tool_input = next(
        (block.input for block in response.content if block.type == "tool_use"),
        None,
    )
    if not isinstance(tool_input, dict):
        raise ValueError("model returned no tool_use block")

    supported = set(_supported_countries())
    partial = False

    seed_events: list[SeedEvent] = []
    for raw in (tool_input.get("seed_events") or [])[:3]:
        try:
            event = SeedEvent.model_validate(raw)
        except Exception as exc:  # noqa: BLE001
            log.warning("extract_events.seed_event_invalid", error=str(exc))
            partial = True
            continue
        event.actor_country = event.actor_country.upper()
        if event.actor_country not in supported:
            partial = True
            continue
        if event.target_country is not None:
            event.target_country = event.target_country.upper()
            if event.target_country not in supported:
                event.target_country = None
        seed_events.append(event)

    selected: list[SelectedCountry] = []
    for raw in (tool_input.get("selected_countries") or [])[:6]:
        try:
            country = SelectedCountry.model_validate(raw)
        except Exception:  # noqa: BLE001
            partial = True
            continue
        if country.iso3.upper() in supported:
            country.iso3 = country.iso3.upper()
            selected.append(country)

    postures = {
        str(code).upper(): str(posture)
        for code, posture in (tool_input.get("posture_overrides") or {}).items()
        if str(code).upper() in supported and str(posture) in _POSTURES
    }

    if not seed_events:
        raise ValueError("no valid seed events extracted")

    return ExtractEventsResponse(
        seed_events=seed_events,
        selected_countries=selected,
        posture_overrides=postures,
        source="partial_fallback" if partial else "llm",
        is_stub=False,
    )


@router.post("/extract-events", response_model=dict[str, Any])
@limiter.limit("10/minute")
async def extract_events(
    request: Request,
    body: ExtractEventsRequest,
) -> dict[str, Any]:
    """Extract structured SeedEvents from an analyst's free-text scenario.

    Degrades to the empty ``is_stub=True`` fallback whenever the LLM path is
    unavailable (no API key) or fails — the frontend flow keeps working, the
    world just starts flat.
    """
    settings = get_settings()
    if not settings.anthropic_api_key:
        log.warning("extract_events.no_api_key_fallback")
        return {"data": _fallback_response().model_dump(mode="json"), "error": None}

    try:
        result = await _run_extraction(body)
    except Exception as exc:  # noqa: BLE001
        log.warning("extract_events.llm_failed_fallback", error=str(exc))
        return {"data": _fallback_response().model_dump(mode="json"), "error": None}

    log.info(
        "extract_events.llm_success",
        source=result.source,
        seed_events=len(result.seed_events),
        countries=len(result.selected_countries),
    )
    return {"data": result.model_dump(mode="json"), "error": None}
