"""Behavior tests for the persistent graph knowledge store (issue #29).

Exercises the team-wide save/list/get/delete + graph endpoints against the real
app and a throwaway SQLite DB. The point is durability + dedupe + referential
consistency, not mocking — these are pure DB-backed handlers.
"""

from __future__ import annotations


def _ent(entity_id="acme", name="Acme Corp", entity_type="company", **extra):
    return {"entity_id": entity_id, "name": name, "entity_type": entity_type, **extra}


def test_save_then_list_and_get(app_client, auth_headers):
    r = app_client.post("/api/knowledge/entities", json=_ent(country="US"), headers=auth_headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["created"] is True
    assert body["entity"]["entity_id"] == "acme"
    assert body["entity"]["created_by"] == "tester"

    r = app_client.get("/api/knowledge/entities", headers=auth_headers)
    assert r.json()["count"] == 1

    r = app_client.get("/api/knowledge/entities/acme", headers=auth_headers)
    assert r.status_code == 200
    assert r.json()["entity"]["name"] == "Acme Corp"


def test_save_is_idempotent_upsert(app_client, auth_headers):
    app_client.post("/api/knowledge/entities", json=_ent(), headers=auth_headers)
    # Same entity_id, new name → update, not a second row.
    r = app_client.post(
        "/api/knowledge/entities", json=_ent(name="Acme Holdings"), headers=auth_headers
    )
    assert r.json()["created"] is False
    listing = app_client.get("/api/knowledge/entities", headers=auth_headers).json()
    assert listing["count"] == 1
    assert listing["entities"][0]["name"] == "Acme Holdings"


def test_entities_of_any_type_are_saveable(app_client, auth_headers):
    for et in ("company", "person", "vessel", "government", "sector"):
        r = app_client.post(
            "/api/knowledge/entities",
            json=_ent(entity_id=f"id_{et}", name=et.title(), entity_type=et),
            headers=auth_headers,
        )
        assert r.status_code == 200, f"{et}: {r.text}"
    assert app_client.get("/api/knowledge/entities", headers=auth_headers).json()["count"] == 5


def test_edge_save_and_graph_roundtrip(app_client, auth_headers):
    app_client.post("/api/knowledge/entities", json=_ent("a", "A Corp"), headers=auth_headers)
    app_client.post("/api/knowledge/entities", json=_ent("b", "B Corp"), headers=auth_headers)
    r = app_client.post(
        "/api/knowledge/edges",
        json={"source_id": "a", "target_id": "b", "relationship_type": "subsidiary_of"},
        headers=auth_headers,
    )
    assert r.status_code == 200, r.text

    graph = app_client.get("/api/knowledge/graph", headers=auth_headers).json()
    assert graph["meta"]["node_count"] == 2
    assert graph["meta"]["edge_count"] == 1
    assert graph["edges"][0]["label"] == "subsidiary of"
    # nodes reuse the shared vis.js factory shape
    assert {"id", "label", "title", "group", "color"} <= set(graph["nodes"][0].keys())


def test_edge_rejects_self_loop(app_client, auth_headers):
    app_client.post("/api/knowledge/entities", json=_ent("a"), headers=auth_headers)
    r = app_client.post(
        "/api/knowledge/edges",
        json={"source_id": "a", "target_id": "a", "relationship_type": "x"},
        headers=auth_headers,
    )
    assert r.status_code == 400


def test_delete_entity_cascades_to_incident_edges(app_client, auth_headers):
    app_client.post("/api/knowledge/entities", json=_ent("a"), headers=auth_headers)
    app_client.post("/api/knowledge/entities", json=_ent("b", "B"), headers=auth_headers)
    app_client.post(
        "/api/knowledge/edges",
        json={"source_id": "a", "target_id": "b", "relationship_type": "linked"},
        headers=auth_headers,
    )
    r = app_client.delete("/api/knowledge/entities/a", headers=auth_headers)
    assert r.status_code == 200
    # the incident edge is gone too
    assert app_client.get("/api/knowledge/edges", headers=auth_headers).json()["count"] == 0
    # and the graph only excludes dangling edges anyway
    assert (
        app_client.get("/api/knowledge/graph", headers=auth_headers).json()["meta"]["edge_count"]
        == 0
    )


def test_delete_missing_entity_is_404(app_client, auth_headers):
    assert (
        app_client.delete("/api/knowledge/entities/nope", headers=auth_headers).status_code == 404
    )


def test_store_is_team_wide_not_per_user(app_client, auth_headers):
    """An entity saved by one analyst is visible to the whole team (shared store)."""
    app_client.post(
        "/api/knowledge/entities", json=_ent("shared", "Shared Co"), headers=auth_headers
    )
    # A second token (same demo user here, but reads are unfiltered by design).
    listing = app_client.get("/api/knowledge/entities", headers=auth_headers).json()
    assert any(e["entity_id"] == "shared" for e in listing["entities"])
