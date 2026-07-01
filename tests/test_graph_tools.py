"""Tests for the agent graph-tools + discovery endpoint (issue #31).

The graph tools let an agent read/write/traverse the persistent knowledge graph
through the same `knowledge_store` seam the HTTP API uses. These tests call the
tool functions directly (as the orchestrator's ToolRegistry would) and assert the
ToolResponse envelope + side effects, then cover the discovery endpoint's
graceful-degradation path (no LLM key in tests).
"""

from __future__ import annotations

import pytest

from src.common.types import ToolResponse
from src.tools.graph import server as gt


@pytest.fixture(autouse=True)
def _clean_db(app_client):
    """Reuse the app_client fixture purely for its fresh per-test SQLite DB."""
    yield


async def _save(entity_id, name="X", entity_type="company"):
    return await gt.graph_save_entity(entity_id, name, entity_type)


@pytest.mark.asyncio
async def test_save_entity_returns_toolresponse_and_persists():
    resp = await gt.graph_save_entity("acme", "Acme Corp", "company", country="US")
    assert isinstance(resp, ToolResponse)
    assert resp.data["created"] is True
    assert resp.data["entity"]["created_by"] == "agent"
    # second save = upsert, not duplicate
    resp2 = await gt.graph_save_entity("acme", "Acme Holdings", "company")
    assert resp2.data["created"] is False
    listing = await gt.graph_list_entities()
    assert listing.data["count"] == 1


@pytest.mark.asyncio
async def test_save_relationship_and_neighbors():
    await _save("a", "A Corp")
    await _save("b", "B Corp")
    edge = await gt.graph_save_relationship("a", "b", "subsidiary_of")
    assert edge.data["created"] is True

    nbrs = await gt.graph_neighbors("a")
    assert nbrs.data["count"] == 1
    assert nbrs.data["neighbors"][0]["entity_id"] == "b"
    assert nbrs.data["neighbors"][0]["direction"] == "out"


@pytest.mark.asyncio
async def test_relationship_rejects_self_loop():
    await _save("a")
    resp = await gt.graph_save_relationship("a", "a", "x")
    assert resp.errors
    assert "self-loop" in resp.errors[0]


@pytest.mark.asyncio
async def test_find_paths_traverses_saved_graph():
    for nid in ("a", "b", "c"):
        await _save(nid, nid.upper())
    await gt.graph_save_relationship("a", "b", "owns")
    await gt.graph_save_relationship("b", "c", "owns")

    resp = await gt.graph_find_paths("a", "c", max_hops=4)
    assert resp.data["path_count"] >= 1
    assert ["a", "b", "c"] in resp.data["paths"]
    assert resp.data["shortest_hops"] == 2

    # no path when disconnected
    await _save("z", "Z")
    assert (await gt.graph_find_paths("a", "z")).data["path_count"] == 0


@pytest.mark.asyncio
async def test_registry_exposes_graph_tools():
    from src.orchestrator.tool_registry import ToolRegistry

    reg = ToolRegistry()
    await reg._ensure_loaded()
    for name in (
        "graph_save_entity",
        "graph_save_relationship",
        "graph_neighbors",
        "graph_find_paths",
        "graph_list_entities",
    ):
        assert name in reg.list_tools()
    assert reg.tool_domain("graph_find_paths") == "graph"


# --- Discovery endpoint -----------------------------------------------------


def test_discover_actions_returns_graph_context_without_llm(app_client, auth_headers):
    # Seed a small graph the discovery handler can ground in.
    app_client.post(
        "/api/knowledge/entities",
        json={"entity_id": "smic", "name": "SMIC", "entity_type": "company"},
        headers=auth_headers,
    )
    app_client.post(
        "/api/knowledge/entities",
        json={"entity_id": "huawei", "name": "Huawei", "entity_type": "company"},
        headers=auth_headers,
    )
    app_client.post(
        "/api/knowledge/edges",
        json={"source_id": "smic", "target_id": "huawei", "relationship_type": "supplies"},
        headers=auth_headers,
    )

    r = app_client.post(
        "/api/discover-actions",
        json={"entity": "SMIC", "proposed_action": "Add to BIS Entity List"},
        headers=auth_headers,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["entity"] == "SMIC"
    assert body["context"]["known_in_graph"] is True
    # neighbor surfaced from the graph
    assert any(n["name"] == "Huawei" for n in body["context"]["neighbors"])
    # no LLM key in tests → graceful empty suggestions + note
    assert body["suggested_actions"] == []
    assert "note" in body
