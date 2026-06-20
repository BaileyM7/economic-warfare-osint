"""Batch sanctions screening endpoint.

Extracted from src/api.py (Phase 2 Stage 3). Screens a list of names against
OFAC SDN + Trade.gov CSL concurrently. Mounted at /api with require_auth applied
at include time (see src/api.py). Behaviour is unchanged from the inline handler.
"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from src.tools.sanctions.client import SanctionsClient

router = APIRouter(prefix="/api", tags=["screening"])


class SanctionsScreenBatchRequest(BaseModel):
    names: list[str]


@router.post("/sanctions/screen-batch")
async def sanctions_screen_batch(req: SanctionsScreenBatchRequest):
    """Screen a list of entity names against OFAC SDN + Trade.gov CSL.

    Returns a dict keyed by the input name with sanctions status for each.
    Runs all checks concurrently; individual failures are reported as not-sanctioned.
    """
    names = [n.strip() for n in req.names if n.strip()][:30]
    if not names:
        return JSONResponse(content={"results": {}})

    client = SanctionsClient()

    async def _check_one(name: str) -> tuple[str, dict]:
        try:
            status = await asyncio.wait_for(client.check_status(name), timeout=10.0)
            return name, {
                "sanctioned": status.is_sanctioned,
                "lists": status.lists_found,
                "programs": status.programs,
            }
        except Exception:
            return name, {"sanctioned": False, "lists": [], "programs": []}

    results = await asyncio.gather(*[_check_one(n) for n in names], return_exceptions=True)
    output: dict[str, dict] = {}
    for r in results:
        if isinstance(r, tuple):
            output[r[0]] = r[1]
    return JSONResponse(content={"results": output})
