"""Semantic similarity / link prediction endpoint (issue #30).

`POST /api/entity/similar` — the customer's top ask, "identify companies with
similar characteristics to this one." Ranks the entities already in the
knowledge store (issues #29/#31) against a target, with an explainable per-result
basis. Backend (lexical vs local embeddings) is chosen by `config.similarity_backend`
and can be overridden per request; the embedding backend degrades to lexical when
the optional `sentence-transformers` dependency is absent.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from src.common import knowledge_store as ks
from src.common import similarity as sim
from src.common.config import config

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/entity", tags=["similarity"])


class SimilarRequest(BaseModel):
    # Identify the target either by its stored entity_id (preferred) or by name +
    # optional traits for an ad-hoc target that need not be saved yet.
    entity_id: str | None = Field(default=None, max_length=200)
    name: str | None = Field(default=None, max_length=300)
    entity_type: str | None = Field(default=None, max_length=50)
    country: str | None = Field(default=None, max_length=100)
    top_k: int = Field(default=5, ge=1, le=50)
    backend: str | None = Field(default=None, description="override: 'lexical' | 'embedding'")


@router.post("/similar")
async def similar_entities(req: SimilarRequest) -> dict:
    """Return the saved entities most similar to the target, ranked with a basis."""
    # Resolve the target entity.
    if req.entity_id:
        target = ks.get_entity(req.entity_id)
        if not target:
            raise HTTPException(status_code=404, detail="Target entity not in knowledge store")
    elif req.name:
        # Prefer an exact saved match; otherwise treat as an ad-hoc target spec.
        matches = ks.list_entities(q=req.name)
        exact = next((m for m in matches if m["name"].lower() == req.name.lower()), None)
        target = exact or {
            "entity_id": None,
            "name": req.name,
            "entity_type": req.entity_type,
            "country": req.country,
            "aliases": [],
            "identifiers": {},
            "notes": "",
        }
    else:
        raise HTTPException(status_code=400, detail="Provide entity_id or name")

    candidates = ks.list_entities()
    backend = (req.backend or config.similarity_backend or "lexical").strip().lower()
    results, backend_used = sim.rank_similar(target, candidates, top_k=req.top_k, backend=backend)

    resp: dict = {
        "target": {
            "entity_id": target.get("entity_id"),
            "name": target.get("name"),
            "entity_type": target.get("entity_type"),
        },
        "backend_used": backend_used,
        "results": results,
        "count": len(results),
    }
    if backend == "embedding" and backend_used != "embedding":
        resp["note"] = (
            "Embedding backend unavailable (install the `similarity` extra); "
            "fell back to the lexical backend."
        )
    return resp
