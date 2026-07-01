"""Target generation & action discovery (issue #31).

Answers the customer's discovery use-case — "I'm thinking about this action for
this company… what are other actions that wouldn't conflict?" — by grounding an
LLM recommendation pass in the persistent knowledge graph (issues #29/#31).

The handler gathers graph context for the entity (its saved record + neighbors
via the `knowledge_store` seam), then reuses the existing CoA generator
(`src/llm.py::generate_recommendations`). When no LLM client is configured
(e.g. offline / CI), it degrades gracefully to an empty suggestion list with a
note rather than failing — the graph context is still returned. Issue #33 will
make the underlying model pluggable.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter
from pydantic import BaseModel, Field

from src.common import knowledge_store as ks
from src.llm import generate_recommendations

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["discovery"])


class DiscoverActionsRequest(BaseModel):
    entity: str = Field(..., min_length=1, max_length=300)
    proposed_action: str | None = Field(default=None, max_length=500)
    entity_type: str | None = Field(default=None, max_length=50)


@router.post("/discover-actions")
async def discover_actions(req: DiscoverActionsRequest) -> dict:
    """Suggest additional, non-conflicting actions for an entity (target generation).

    Grounds the suggestions in whatever the knowledge graph already knows about
    the entity (saved record + direct neighbors), so recommendations reference
    real connected entities rather than being generated in a vacuum.
    """
    entity = req.entity.strip()

    # Graph context: the saved entity (if any) + its neighbors. Matching is by
    # name (case-insensitive) so the caller can pass a display name, not an id.
    saved = ks.list_entities(q=entity)
    neighbors: list[dict] = []
    if saved:
        neighbors = ks.neighbors(saved[0]["entity_id"])

    context = {
        "entity": entity,
        "entity_type": req.entity_type or (saved[0]["entity_type"] if saved else None),
        "proposed_action": req.proposed_action,
        "known_in_graph": bool(saved),
        "neighbors": [
            {"name": n["name"], "relationship": n["relationship_type"]} for n in neighbors
        ],
    }

    if req.proposed_action:
        question = (
            f'I am considering this action against {entity}: "{req.proposed_action}". '
            "Recommend 3-4 ADDITIONAL actions that complement it and would NOT conflict "
            "with it or with each other. For each, note why it does not conflict. "
            "Use the graph context (connected entities) to find adjacent, non-overlapping levers."
        )
    else:
        question = (
            f"Generate 3-4 distinct, non-conflicting candidate actions to consider against "
            f"{entity}, using the graph context (connected entities) to diversify the levers."
        )

    suggestions = await generate_recommendations(context, analyst_question=question)

    resp: dict = {
        "entity": entity,
        "proposed_action": req.proposed_action,
        "suggested_actions": suggestions,
        "context": context,
    }
    if not suggestions:
        resp["note"] = (
            "No LLM client configured — returning graph context only. "
            "Set ANTHROPIC_API_KEY (or a local model via issue #33) to generate suggestions."
        )
    return resp
