"""Team-wide collection priorities API (issue #32).

Lets the team declare what to prioritize at the country / sector / company level.
Priorities are **global / shared**: any analyst can read them (so the whole team
sees the same priorities), but only an **admin** can set or remove them — mirroring
the existing admin-managed configuration surfaces.

All logic goes through the `src/common/priorities.py` seam, shared with the
risk-feed ranking so the matching rule lives in one place.

Endpoints (mounted at /api/priorities):
  GET    /api/priorities            list priorities (auth; optional ?level=)
  POST   /api/priorities            upsert a priority (admin; dedupe on level+key)
  DELETE /api/priorities/{id}       remove a priority (admin)
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from src.auth import require_admin, require_auth
from src.common import priorities as pr
from src.db import log_activity

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/priorities", tags=["priorities"])


class PriorityRequest(BaseModel):
    level: str = Field(..., description="country | sector | company")
    key: str = Field(..., min_length=1, max_length=120)
    weight: float = Field(default=1.0, ge=0.0, le=100.0)
    label: str = Field(default="", max_length=200)
    notes: str = Field(default="", max_length=1000)


@router.get("")
async def list_priorities(
    level: str | None = Query(default=None, max_length=20),
    username: str = Depends(require_auth),
) -> dict:
    """List team priorities (any authenticated analyst can read)."""
    if level and level not in pr.LEVELS:
        raise HTTPException(status_code=400, detail=f"level must be one of {pr.LEVELS}")
    items = pr.list_priorities(level=level)
    return {"priorities": items, "count": len(items)}


@router.post("")
async def set_priority(req: PriorityRequest, username: str = Depends(require_admin)) -> dict:
    """Create or update a priority (admin only)."""
    if req.level not in pr.LEVELS:
        raise HTTPException(status_code=400, detail=f"level must be one of {pr.LEVELS}")
    priority, created = pr.upsert_priority(
        level=req.level,
        key=req.key.strip(),
        weight=req.weight,
        label=req.label,
        notes=req.notes,
        created_by=username,
    )
    log_activity(
        "priority_set",
        f"Priority {req.level}:{req.key} set to weight {req.weight}",
        source=username,
    )
    return {"priority": priority, "created": created}


@router.delete("/{priority_id}")
async def remove_priority(priority_id: str, username: str = Depends(require_admin)) -> dict:
    if not pr.delete_priority(priority_id):
        raise HTTPException(status_code=404, detail="Priority not found")
    return {"deleted": priority_id}
