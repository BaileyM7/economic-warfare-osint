"""Long-term memory API (Phase 5) — let analysts see and control their memory.

An intel tool that quietly accumulates "facts" about what an analyst investigated
must let that analyst inspect and delete them. This is not optional: it's the
control that makes the memory-poisoning guardrails auditable in practice — if a
bad fact slips through extraction, the analyst can see it and kill it.

Everything is scoped to the authenticated username, enforced in the seam
(`src/common/agent_memory.py`), never from a request body.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query

from src.auth import require_auth
from src.common import agent_memory

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/memory", tags=["memory"])


@router.get("")
async def list_my_memories(
    username: str = Depends(require_auth),
    limit: int = Query(default=100, ge=1, le=500),
) -> dict:
    """Everything the system has remembered about this analyst's prior work."""
    memories = agent_memory.list_memories(username, limit=limit)
    return {
        "memories": memories,
        "count": len(memories),
        "backend": agent_memory.memory_backend_name(),
    }


@router.get("/search")
async def search_my_memories(
    q: str = Query(..., min_length=1, max_length=500),
    username: str = Depends(require_auth),
    k: int = Query(default=8, ge=1, le=50),
) -> dict:
    """Semantic (or lexical-fallback) search over this analyst's own memories."""
    hits = await agent_memory.recall(q, user_id=username, k=k)
    return {
        "backend": agent_memory.memory_backend_name(),
        "results": [{"memory": m, "score": round(score, 4)} for m, score in hits],
        "count": len(hits),
    }


@router.delete("/{memory_id}")
async def forget_memory(memory_id: str, username: str = Depends(require_auth)) -> dict:
    """Delete one of the caller's memories. 404 if it isn't theirs / doesn't exist."""
    if not agent_memory.forget(memory_id, username):
        raise HTTPException(status_code=404, detail="Memory not found")
    return {"deleted": memory_id}
