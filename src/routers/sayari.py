"""Sayari Graph endpoints — entity resolution, traversal, and UBO lookups.

Extracted from src/api.py (Phase 2 Stage 3). Thin async wrappers over the Sayari
REST client, mounted at /api/sayari with require_auth applied at include time
(see src/api.py). Behaviour is unchanged from the original inline handlers.
"""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/sayari", tags=["sayari"])


class SayariResolveRequest(BaseModel):
    query: str
    limit: int = 5
    entity_type: str | None = None


class SayariEntityRequest(BaseModel):
    entity_id: str


class SayariTraversalRequest(BaseModel):
    entity_id: str
    depth: int = 1
    limit: int = 20


@router.post("/resolve")
async def sayari_resolve_endpoint(req: SayariResolveRequest):
    """Resolve a name to Sayari entity IDs."""
    from src.tools.sayari.rest_client import get_sayari_client

    try:
        client = get_sayari_client()
        result = await asyncio.wait_for(
            client.resolve(req.query.strip(), limit=req.limit, entity_type=req.entity_type),
            timeout=15.0,
        )
        return JSONResponse(content=result.model_dump(mode="json"))
    except Exception as e:
        logger.warning("Sayari resolve error: %s", e)
        raise HTTPException(status_code=502, detail=str(e))


@router.post("/related")
async def sayari_related_endpoint(req: SayariTraversalRequest):
    """Get entities related to a Sayari entity (graph traversal)."""
    from src.tools.sayari.rest_client import get_sayari_client

    try:
        client = get_sayari_client()
        result = await asyncio.wait_for(
            client.get_traversal(req.entity_id, depth=req.depth, limit=req.limit),
            timeout=20.0,
        )
        return JSONResponse(content=result.model_dump(mode="json"))
    except Exception as e:
        logger.warning("Sayari traversal error: %s", e)
        raise HTTPException(status_code=502, detail=str(e))


@router.post("/ubo")
async def sayari_ubo_endpoint(req: SayariEntityRequest):
    """Get ultimate beneficial owners of a Sayari entity."""
    from src.tools.sayari.rest_client import get_sayari_client

    try:
        client = get_sayari_client()
        result = await asyncio.wait_for(
            client.get_ubo(req.entity_id),
            timeout=20.0,
        )
        return JSONResponse(content=result.model_dump(mode="json"))
    except Exception as e:
        logger.warning("Sayari UBO error: %s", e)
        raise HTTPException(status_code=502, detail=str(e))
