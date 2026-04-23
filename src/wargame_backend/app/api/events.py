"""Events API — data-lake events and simulation events.

Endpoints:
  GET /api/events           Raw data-lake events with filters (cursor-paginated)
  GET /api/sim-events       SimEvents for a given simulation (cursor-paginated)
"""

from __future__ import annotations

import base64
import json
import uuid
from datetime import datetime
from typing import Annotated, Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from wargame_backend.app.db.models import Event, SimEvent
from wargame_backend.app.deps import get_db

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/api", tags=["events"])


# ---------------------------------------------------------------------------
# Cursor helpers (encodes/decodes (occurred_at|timestamp, id) tuples)
# ---------------------------------------------------------------------------


def _encode_cursor(dt: datetime, row_id: uuid.UUID) -> str:
    """Encode a (datetime, UUID) pair as a base64 opaque cursor."""
    raw = json.dumps({"ts": dt.isoformat(), "id": str(row_id)})
    return base64.urlsafe_b64encode(raw.encode()).decode()


def _decode_cursor(cursor: str) -> tuple[datetime, uuid.UUID]:
    """Decode a cursor back to (datetime, UUID).  Raises ValueError on bad input."""
    try:
        raw = base64.urlsafe_b64decode(cursor.encode()).decode()
        data = json.loads(raw)
        return datetime.fromisoformat(data["ts"]), uuid.UUID(data["id"])
    except Exception as exc:
        raise ValueError(f"Invalid cursor: {cursor!r}") from exc


# ---------------------------------------------------------------------------
# GET /api/events  — raw data-lake events
# ---------------------------------------------------------------------------


@router.get("/events", response_model=dict[str, Any])
async def list_events(
    source: Annotated[str | None, Query(description="Filter by source key, e.g. 'gdelt'.")] = None,
    actor_iso3: Annotated[str | None, Query(description="Filter by actor country ISO-3.")] = None,
    target_iso3: Annotated[str | None, Query(description="Filter by target country ISO-3.")] = None,
    domain: Annotated[str | None, Query(description="Filter by event domain.")] = None,
    from_: Annotated[
        datetime | None, Query(alias="from", description="occurred_at >= this value (ISO-8601).")
    ] = None,
    to: Annotated[
        datetime | None, Query(description="occurred_at <= this value (ISO-8601).")
    ] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    cursor: Annotated[str | None, Query(description="Opaque pagination cursor.")] = None,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """List raw data-lake events with optional filters.  Cursor-paginated."""
    conditions = []

    if source:
        conditions.append(Event.source == source)
    if actor_iso3:
        conditions.append(Event.actor_iso3 == actor_iso3.upper())
    if target_iso3:
        conditions.append(Event.target_iso3 == target_iso3.upper())
    if domain:
        conditions.append(Event.domain == domain)
    if from_:
        conditions.append(Event.occurred_at >= from_)
    if to:
        conditions.append(Event.occurred_at <= to)

    if cursor:
        try:
            cursor_dt, cursor_id = _decode_cursor(cursor)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        # Keyset pagination: (occurred_at DESC, id DESC)
        conditions.append(
            (Event.occurred_at < cursor_dt)
            | and_(Event.occurred_at == cursor_dt, Event.id < cursor_id)
        )

    stmt = (
        select(Event)
        .where(*conditions)
        .order_by(Event.occurred_at.desc(), Event.id.desc())
        .limit(limit + 1)  # fetch one extra to detect has_more
    )

    rows = (await db.execute(stmt)).scalars().all()
    has_more = len(rows) > limit
    items = list(rows[:limit])

    next_cursor: str | None = None
    if has_more and items:
        last = items[-1]
        next_cursor = _encode_cursor(last.occurred_at, last.id)

    return {
        "data": {
            "items": [_event_to_dict(e) for e in items],
            "next_cursor": next_cursor,
            "has_more": has_more,
        },
        "error": None,
    }


def _event_to_dict(e: Event) -> dict[str, Any]:
    return {
        "id": str(e.id),
        "data_source_id": str(e.data_source_id) if e.data_source_id else None,
        "source": e.source,
        "occurred_at": e.occurred_at.isoformat(),
        "actor_iso3": e.actor_iso3,
        "target_iso3": e.target_iso3,
        "event_type": e.event_type,
        "domain": e.domain.value if e.domain else None,
        "severity": float(e.severity) if e.severity is not None else None,
        "payload": e.payload,
        "raw_text": e.raw_text,
        "ingested_at": e.ingested_at.isoformat(),
    }


# ---------------------------------------------------------------------------
# GET /api/sim-events  — simulation events
# ---------------------------------------------------------------------------


@router.get("/sim-events", response_model=dict[str, Any])
async def list_sim_events(
    simulation_id: Annotated[
        uuid.UUID, Query(description="UUID of the simulation to fetch events for.")
    ],
    turn: Annotated[int | None, Query(ge=0, description="Filter by turn number.")] = None,
    actor_country: Annotated[
        str | None, Query(description="Filter by actor ISO-3 code.")
    ] = None,
    domain: Annotated[str | None, Query(description="Filter by event domain.")] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    cursor: Annotated[str | None, Query(description="Opaque pagination cursor.")] = None,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """List SimEvents for a given simulation.  Cursor-paginated."""
    conditions = [SimEvent.sim_id == simulation_id]

    if turn is not None:
        conditions.append(SimEvent.turn == turn)
    if actor_country:
        conditions.append(SimEvent.actor_country == actor_country.upper())
    if domain:
        conditions.append(SimEvent.domain == domain)

    if cursor:
        try:
            cursor_dt, cursor_id = _decode_cursor(cursor)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        conditions.append(
            (SimEvent.timestamp < cursor_dt)
            | and_(SimEvent.timestamp == cursor_dt, SimEvent.id < cursor_id)
        )

    stmt = (
        select(SimEvent)
        .where(*conditions)
        .order_by(SimEvent.timestamp.asc(), SimEvent.id.asc())
        .limit(limit + 1)
    )

    rows = (await db.execute(stmt)).scalars().all()
    has_more = len(rows) > limit
    items = list(rows[:limit])

    next_cursor: str | None = None
    if has_more and items:
        last = items[-1]
        next_cursor = _encode_cursor(last.timestamp, last.id)

    return {
        "data": {
            "items": [_sim_event_to_dict(e) for e in items],
            "next_cursor": next_cursor,
            "has_more": has_more,
        },
        "error": None,
    }


def _sim_event_to_dict(e: SimEvent) -> dict[str, Any]:
    return {
        "id": str(e.id),
        "sim_id": str(e.sim_id),
        "parent_event_id": str(e.parent_event_id) if e.parent_event_id else None,
        "turn": e.turn,
        "actor_country": e.actor_country,
        "target_country": e.target_country,
        "domain": e.domain.value,
        "action_type": e.action_type,
        "payload": e.payload,
        "rationale": e.rationale,
        "citations": e.citations,
        "escalation_rung": e.escalation_rung,
        "timestamp": e.timestamp.isoformat(),
    }
