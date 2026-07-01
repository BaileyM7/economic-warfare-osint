"""Persistent graph knowledge store HTTP API (issue #29).

A **team-wide / shared** store: any authenticated analyst can save entities of
any type and the relationships between them, and every analyst sees the same
graph. Persistence lives in SQLite (durable — not the in-memory pattern that
loses data on restart, fragility F3).

These handlers are thin: all reads/writes go through `src/common/knowledge_store.py`,
the single seam shared with the agent graph-tools (`src/tools/graph/server.py`),
so the tool path can never drift from the endpoint path.

`created_by` records which analyst first saved a row (provenance); reads are not
filtered by user, by design (contrast with the per-user watchlist router).

Endpoints (mounted at /api/knowledge, require_auth applied at include time):
  POST   /api/knowledge/entities            upsert an entity (dedupe on entity_id)
  GET    /api/knowledge/entities            list saved entities (optional ?q=)
  GET    /api/knowledge/entities/{entity_id}  fetch one
  DELETE /api/knowledge/entities/{entity_id}  remove one (+ its incident edges)
  POST   /api/knowledge/edges               upsert an edge
  GET    /api/knowledge/edges               list edges (optional ?entity_id=)
  DELETE /api/knowledge/edges/{edge_id}     remove one
  GET    /api/knowledge/graph               saved store as a vis.js graph
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from src.auth import require_auth
from src.common import knowledge_store as ks
from src.db import log_activity

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/knowledge", tags=["knowledge"])


# --- Request models ---------------------------------------------------------


class SaveEntityRequest(BaseModel):
    entity_id: str = Field(..., min_length=1, max_length=200)
    name: str = Field(..., min_length=1, max_length=300)
    entity_type: str = Field(..., min_length=1, max_length=50)
    country: str | None = Field(default=None, max_length=100)
    aliases: list[str] = Field(default_factory=list)
    identifiers: dict[str, str] = Field(default_factory=dict)
    notes: str = Field(default="", max_length=2000)


class SaveEdgeRequest(BaseModel):
    source_id: str = Field(..., min_length=1, max_length=200)
    target_id: str = Field(..., min_length=1, max_length=200)
    relationship_type: str = Field(..., min_length=1, max_length=100)
    properties: dict = Field(default_factory=dict)
    confidence: str = Field(default="MEDIUM")


# --- Entity endpoints -------------------------------------------------------


@router.post("/entities")
async def save_entity(req: SaveEntityRequest, username: str = Depends(require_auth)) -> dict:
    """Upsert an entity into the shared store, keyed by ``entity_id``."""
    entity, created = ks.upsert_entity(
        entity_id=req.entity_id,
        name=req.name,
        entity_type=req.entity_type,
        country=req.country,
        aliases=req.aliases,
        identifiers=req.identifiers,
        notes=req.notes,
        created_by=username,
    )
    if created:
        log_activity(
            "entity_saved", f"Entity '{req.name}' saved to knowledge graph", source=username
        )
    return {"entity": entity, "created": created}


@router.get("/entities")
async def list_entities(
    q: str | None = Query(default=None, max_length=200),
    entity_type: str | None = Query(default=None, max_length=50),
) -> dict:
    """List saved entities (team-wide). Optional case-insensitive name filter ``q``."""
    entities = ks.list_entities(q=q, entity_type=entity_type)
    return {"entities": entities, "count": len(entities)}


@router.get("/entities/{entity_id}")
async def get_entity(entity_id: str) -> dict:
    entity = ks.get_entity(entity_id)
    if not entity:
        raise HTTPException(status_code=404, detail="Entity not found in knowledge store")
    return {"entity": entity}


@router.delete("/entities/{entity_id}")
async def delete_entity(entity_id: str, username: str = Depends(require_auth)) -> dict:
    """Remove an entity and any edges incident to it (keeps the graph consistent)."""
    if not ks.delete_entity(entity_id):
        raise HTTPException(status_code=404, detail="Entity not found in knowledge store")
    return {"deleted": entity_id}


# --- Edge endpoints ---------------------------------------------------------


@router.post("/edges")
async def save_edge(req: SaveEdgeRequest, username: str = Depends(require_auth)) -> dict:
    """Upsert a relationship, deduped on (source_id, target_id, relationship_type)."""
    if req.source_id == req.target_id:
        raise HTTPException(status_code=400, detail="An edge cannot link an entity to itself")
    edge, created = ks.upsert_edge(
        source_id=req.source_id,
        target_id=req.target_id,
        relationship_type=req.relationship_type,
        properties=req.properties,
        confidence=req.confidence,
        created_by=username,
    )
    return {"edge": edge, "created": created}


@router.get("/edges")
async def list_edges(entity_id: str | None = Query(default=None, max_length=200)) -> dict:
    """List saved edges. With ``entity_id``, only edges incident to that entity."""
    edges = ks.list_edges(entity_id=entity_id)
    return {"edges": edges, "count": len(edges)}


@router.delete("/edges/{edge_id}")
async def delete_edge(edge_id: str, username: str = Depends(require_auth)) -> dict:
    if not ks.delete_edge(edge_id):
        raise HTTPException(status_code=404, detail="Edge not found in knowledge store")
    return {"deleted": edge_id}


# --- Graph view -------------------------------------------------------------


@router.get("/graph")
async def knowledge_graph() -> dict:
    """Return the whole saved store as a vis.js graph, ready to load into GraphViewer."""
    return ks.to_vis_graph()
