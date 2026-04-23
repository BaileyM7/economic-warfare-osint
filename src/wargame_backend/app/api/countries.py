"""Countries API — GET /api/countries and GET /api/countries/{iso3}.

Reads from the ``countries`` table.  If the table is empty it falls back to
loading ``src/shared/seeds/countries.yaml`` (if it exists) and seeding the
database transparently.
"""

from __future__ import annotations

import pathlib
from typing import Annotated, Any

import structlog
import yaml
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from wargame_backend.app.db.models import Country, CountryRelationship
from wargame_backend.app.deps import get_db

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/api/countries", tags=["countries"])

# Path to the optional seed YAML (may not exist until Phase 3c)
# parents[0]=api, [1]=app, [2]=wargame_backend, [3]=src → then wargame_shared/seeds/countries.yaml
_SEEDS_PATH = pathlib.Path(__file__).parents[3] / "wargame_shared" / "seeds" / "countries.yaml"

if not _SEEDS_PATH.exists():
    import structlog as _sl
    _sl.get_logger(__name__).warning(
        "countries_seed_file_missing",
        path=str(_SEEDS_PATH),
        hint="Globe will render empty until the seed file is present at src/wargame_shared/seeds/countries.yaml",
    )


# ---------------------------------------------------------------------------
# Response schemas
# ---------------------------------------------------------------------------


class CountryResponse(BaseModel):
    """Single country representation."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    iso3: str
    name: str
    gdp_usd: float | None = None
    profile: dict[str, Any] = Field(default_factory=dict)
    doctrine: dict[str, Any] = Field(default_factory=dict)
    red_lines: list[Any] = Field(default_factory=list)
    military_assets: dict[str, Any] = Field(default_factory=dict)
    persona: str | None = None
    updated_at: str


class CountryListResponse(BaseModel):
    """Paginated list of countries."""

    items: list[CountryResponse]
    total: int
    limit: int
    offset: int


# ---------------------------------------------------------------------------
# Seed helper
# ---------------------------------------------------------------------------


async def _maybe_seed_countries(db: AsyncSession) -> None:
    """Load countries.yaml into the DB if the table is empty and the file exists."""
    if not _SEEDS_PATH.exists():
        return

    count_result = await db.execute(select(func.count()).select_from(Country))
    count = count_result.scalar_one()
    if count > 0:
        return

    log.info("Seeding countries from YAML", path=str(_SEEDS_PATH))
    with _SEEDS_PATH.open(encoding="utf-8") as fh:
        raw = yaml.safe_load(fh) or {}

    # Accept either a top-level list OR a dict with a "countries" key — the
    # seed YAML uses the latter to match what the AI sim engine reads.
    records: list[dict[str, Any]]
    if isinstance(raw, dict):
        records = list(raw.get("countries", []))
    elif isinstance(raw, list):
        records = raw
    else:
        records = []

    for record in records:
        if not isinstance(record, dict):
            continue

        # Doctrine can be a free-form string in the seed file; normalize to a
        # JSONB-friendly dict so the schema stays structured.
        doctrine_raw = record.get("doctrine")
        if isinstance(doctrine_raw, str):
            doctrine_value: dict[str, Any] = {"text": doctrine_raw.strip()}
        elif isinstance(doctrine_raw, dict):
            doctrine_value = doctrine_raw
        else:
            doctrine_value = {}

        # red_lines in the seed is a list of strings; model expects JSONB list.
        red_lines_raw = record.get("red_lines") or []
        red_lines_value: list[Any] = (
            list(red_lines_raw) if isinstance(red_lines_raw, list) else []
        )

        # Resolve persona_file (relative to the seeds dir) into inline markdown.
        persona_value: str | None = None
        persona_ref = record.get("persona_file")
        if isinstance(persona_ref, str) and persona_ref.strip():
            persona_path = (_SEEDS_PATH.parent / persona_ref).resolve()
            try:
                persona_value = persona_path.read_text(encoding="utf-8")
            except OSError as exc:
                log.warning(
                    "persona_file_missing",
                    iso3=record.get("iso3"),
                    path=str(persona_path),
                    error=str(exc),
                )

        # Fold remaining strategic metadata (lat/lon/government_type/alliances
        # /adversaries/military_spend_usd) into profile so nothing is lost.
        _core_keys = {"iso3", "name", "doctrine", "red_lines", "gdp_usd",
                      "profile", "military_assets", "persona_file"}
        profile_extras = {k: v for k, v in record.items() if k not in _core_keys}
        profile_value = {**record.get("profile", {}), **profile_extras}

        country = Country(
            iso3=record.get("iso3", "").upper(),
            name=record.get("name", ""),
            profile=profile_value,
            doctrine=doctrine_value,
            red_lines=red_lines_value,
            military_assets=record.get("military_assets", {}),
            persona=persona_value,
            gdp_usd=record.get("gdp_usd"),
        )
        db.add(country)

    await db.commit()
    log.info("Country seed complete", count=len(records))


def _country_to_response(c: Country) -> CountryResponse:
    """Convert an ORM Country to a CountryResponse."""
    return CountryResponse(
        id=str(c.id),
        iso3=c.iso3,
        name=c.name,
        gdp_usd=float(c.gdp_usd) if c.gdp_usd is not None else None,
        profile=c.profile,
        doctrine=c.doctrine,
        red_lines=c.red_lines,
        military_assets=c.military_assets,
        persona=c.persona,
        updated_at=c.updated_at.isoformat(),
    )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("", response_model=dict[str, Any])
async def list_countries(
    iso3: Annotated[
        str | None,
        Query(
            description=(
                "Comma-separated ISO-3 codes to filter by, e.g. 'CHN,USA'. "
                "When omitted, all countries are returned."
            )
        ),
    ] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """List all countries, with optional ISO-3 filter and pagination."""
    await _maybe_seed_countries(db)

    stmt = select(Country).order_by(Country.name)

    if iso3:
        codes = [c.strip().upper() for c in iso3.split(",") if c.strip()]
        stmt = stmt.where(Country.iso3.in_(codes))

    # Total count for pagination
    count_stmt = select(func.count()).select_from(stmt.subquery())
    total: int = (await db.execute(count_stmt)).scalar_one()

    # Paginated rows
    rows = (await db.execute(stmt.offset(offset).limit(limit))).scalars().all()

    return {
        "data": {
            "items": [_country_to_response(c).model_dump(mode="json") for c in rows],
            "total": total,
            "limit": limit,
            "offset": offset,
        },
        "error": None,
    }


@router.get("/{iso3_code}", response_model=dict[str, Any])
async def get_country(
    iso3_code: str,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Fetch a single country by ISO-3 code, including bilateral relationships."""
    await _maybe_seed_countries(db)

    result = await db.execute(
        select(Country).where(Country.iso3 == iso3_code.upper())
    )
    country = result.scalar_one_or_none()
    if country is None:
        raise HTTPException(status_code=404, detail=f"Country '{iso3_code}' not found.")

    # Load bilateral relationships for this country
    rel_result = await db.execute(
        select(CountryRelationship).where(
            (CountryRelationship.country_a_id == country.id)
            | (CountryRelationship.country_b_id == country.id)
        )
    )
    relationships = rel_result.scalars().all()

    rel_list = [
        {
            "id": str(r.id),
            "country_a_id": str(r.country_a_id),
            "country_b_id": str(r.country_b_id),
            "posture": r.posture.value,
            "trust_score": float(r.trust_score),
            "alliance_memberships": r.alliance_memberships,
        }
        for r in relationships
    ]

    payload = _country_to_response(country).model_dump(mode="json")
    payload["relationships"] = rel_list

    return {"data": payload, "error": None}
