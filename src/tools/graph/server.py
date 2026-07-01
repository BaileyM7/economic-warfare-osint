"""MCP server exposing graph tools — let agents *work on* the knowledge graph (issue #31).

These are the tools the orchestrator can call to read, write, and traverse the
persistent, team-wide knowledge graph (issues #29/#31), beyond the existing
read-only risk reports. Every tool returns the standard `ToolResponse` envelope
and goes through the shared `src/common/knowledge_store.py` seam — the same code
the HTTP endpoints use — so the agent path can never drift from the API path.

All tools are deterministic (local SQLite + networkx); no external API or LLM
call, so they're fast and fully testable offline.
"""

from __future__ import annotations

import logging
from datetime import datetime

from mcp.server.fastmcp import FastMCP

from src.common import knowledge_store as ks
from src.common.types import Confidence, SourceReference, ToolResponse

logger = logging.getLogger(__name__)

mcp = FastMCP("graph")

# Agent-authored rows are attributed to "agent" so provenance distinguishes them
# from analyst-saved entities (which carry the analyst's username).
_AGENT = "agent"


def _store_source() -> SourceReference:
    return SourceReference(
        name="Emissary Knowledge Store",
        description="Persistent team-wide entity graph (saved_entities / saved_edges)",
        accessed_at=datetime.utcnow(),
    )


@mcp.tool()
async def graph_save_entity(
    entity_id: str,
    name: str,
    entity_type: str,
    country: str | None = None,
    notes: str = "",
) -> ToolResponse:
    """Persist an entity of any type to the shared knowledge graph (upsert on entity_id).

    Use this to record an entity the analysis surfaced so it survives the session
    and other analysts (and later tool calls) can see it.
    """
    entity, created = ks.upsert_entity(
        entity_id=entity_id,
        name=name,
        entity_type=entity_type,
        country=country,
        notes=notes,
        created_by=_AGENT,
    )
    return ToolResponse(
        data={"entity": entity, "created": created},
        confidence=Confidence.HIGH,
        sources=[_store_source()],
    )


@mcp.tool()
async def graph_save_relationship(
    source_id: str,
    target_id: str,
    relationship_type: str,
    confidence: str = "MEDIUM",
) -> ToolResponse:
    """Persist a directed relationship between two saved entities (upsert on the triple)."""
    if source_id == target_id:
        return ToolResponse(
            data={"error": "An edge cannot link an entity to itself"},
            confidence=Confidence.LOW,
            sources=[_store_source()],
            errors=["self-loop rejected"],
        )
    edge, created = ks.upsert_edge(
        source_id=source_id,
        target_id=target_id,
        relationship_type=relationship_type,
        confidence=confidence,
        created_by=_AGENT,
    )
    return ToolResponse(
        data={"edge": edge, "created": created},
        confidence=Confidence.HIGH,
        sources=[_store_source()],
    )


@mcp.tool()
async def graph_list_entities(query: str | None = None) -> ToolResponse:
    """List entities in the knowledge graph, optionally filtered by a name substring."""
    entities = ks.list_entities(q=query)
    return ToolResponse(
        data={"entities": entities, "count": len(entities)},
        confidence=Confidence.HIGH,
        sources=[_store_source()],
    )


@mcp.tool()
async def graph_neighbors(entity_id: str) -> ToolResponse:
    """Return the direct neighbors of an entity, with the connecting relationship."""
    nbrs = ks.neighbors(entity_id)
    return ToolResponse(
        data={"entity_id": entity_id, "neighbors": nbrs, "count": len(nbrs)},
        confidence=Confidence.HIGH if nbrs else Confidence.LOW,
        sources=[_store_source()],
    )


@mcp.tool()
async def graph_find_paths(source_id: str, target_id: str, max_hops: int = 4) -> ToolResponse:
    """Find connecting paths between two entities in the saved graph (exposure chains).

    Treats the graph as undirected — exposure can run either way — and caps paths
    at ``max_hops`` edges. Returns a list of paths, each a list of entity_ids.
    """
    paths = ks.find_paths(source_id, target_id, max_hops=max_hops)
    return ToolResponse(
        data={
            "source_id": source_id,
            "target_id": target_id,
            "paths": paths,
            "path_count": len(paths),
            "shortest_hops": (min(len(p) - 1 for p in paths) if paths else None),
        },
        confidence=Confidence.HIGH if paths else Confidence.LOW,
        sources=[_store_source()],
    )
