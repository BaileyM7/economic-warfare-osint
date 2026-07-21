"""Single seam for the persistent graph knowledge store (issues #29 + #31).

Both the HTTP router (`src/routers/knowledge.py`) and the agent graph-tools
(`src/tools/graph/server.py`) go through this module instead of writing their own
SQL, so there is exactly one place that knows the `saved_entities` / `saved_edges`
schema. That avoids the "split feature / bypassed seam" fragility: a tool can
never drift from the endpoint, because they call the same functions.

All functions are synchronous and self-contained (open + close their own
connection via `src.db.get_db`); callers in async contexts can invoke them
directly — SQLite calls here are short and non-blocking in practice, matching the
rest of the app's DB access pattern.
"""

from __future__ import annotations

import json
import logging

from src.db import _new_id, _now, get_db, row_to_saved_edge, row_to_saved_entity

logger = logging.getLogger(__name__)


# --- Vector-index write-through ---------------------------------------------
# Imported lazily and wrapped: the index is an optional accelerator (it needs
# Redis 8 + a Voyage key), and this module is the system of record. Nothing here
# may raise into a caller that is just trying to save a row.


def _enqueue_reindex(entity: dict) -> None:
    try:
        from src.common.vector_index import enqueue_reindex

        enqueue_reindex(entity)
    except Exception as exc:  # noqa: BLE001
        logger.debug("Vector reindex enqueue skipped (non-fatal): %s", exc)


def _enqueue_removal(entity_id: str) -> None:
    try:
        from src.common.vector_index import enqueue_removal

        enqueue_removal(entity_id)
    except Exception as exc:  # noqa: BLE001
        logger.debug("Vector removal enqueue skipped (non-fatal): %s", exc)


# --- Entities ---------------------------------------------------------------


def upsert_entity(
    entity_id: str,
    name: str,
    entity_type: str,
    country: str | None = None,
    aliases: list[str] | None = None,
    identifiers: dict[str, str] | None = None,
    notes: str = "",
    created_by: str | None = None,
) -> tuple[dict, bool]:
    """Insert or update an entity keyed by ``entity_id``.

    Returns ``(entity_dict, created)``. On update, the original ``created_by`` /
    ``created_at`` provenance is preserved.
    """
    now = _now()
    conn = get_db()
    try:
        existing = conn.execute(
            "SELECT id FROM saved_entities WHERE entity_id = ?", (entity_id,)
        ).fetchone()
        if existing:
            conn.execute(
                "UPDATE saved_entities SET name = ?, entity_type = ?, country = ?, "
                "aliases = ?, identifiers = ?, notes = ?, updated_at = ? WHERE entity_id = ?",
                (
                    name,
                    entity_type,
                    country,
                    json.dumps(aliases or []),
                    json.dumps(identifiers or {}),
                    notes,
                    now,
                    entity_id,
                ),
            )
            created = False
        else:
            conn.execute(
                "INSERT INTO saved_entities (id, entity_id, name, entity_type, country, "
                "aliases, identifiers, notes, created_by, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    _new_id(),
                    entity_id,
                    name,
                    entity_type,
                    country,
                    json.dumps(aliases or []),
                    json.dumps(identifiers or {}),
                    notes,
                    created_by,
                    now,
                    now,
                ),
            )
            created = True
        conn.commit()
        row = conn.execute(
            "SELECT * FROM saved_entities WHERE entity_id = ?", (entity_id,)
        ).fetchone()
    finally:
        conn.close()

    entity = row_to_saved_entity(row)
    # Keep the Redis vector index in sync — AFTER the commit, and best-effort.
    # This function stays synchronous and must never block on (or fail because of)
    # a network call: embedding takes ~200ms and can time out, and "Voyage is slow"
    # must never mean "saving an entity failed". The row is already durable; the
    # index catches up via a fire-and-forget task plus a durable dirty set.
    _enqueue_reindex(entity)
    return entity, created


def get_entity(entity_id: str) -> dict | None:
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT * FROM saved_entities WHERE entity_id = ?", (entity_id,)
        ).fetchone()
    finally:
        conn.close()
    return row_to_saved_entity(row) if row else None


def list_entities(q: str | None = None, entity_type: str | None = None) -> list[dict]:
    conn = get_db()
    try:
        sql = "SELECT * FROM saved_entities"
        clauses: list[str] = []
        params: list = []
        if q:
            clauses.append("LOWER(name) LIKE ?")
            params.append(f"%{q.lower()}%")
        if entity_type:
            clauses.append("entity_type = ?")
            params.append(entity_type)
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY updated_at DESC"
        rows = conn.execute(sql, params).fetchall()
    finally:
        conn.close()
    return [row_to_saved_entity(r) for r in rows]


def delete_entity(entity_id: str) -> int:
    """Delete an entity and any edges incident to it. Returns rows deleted (0 or 1)."""
    conn = get_db()
    try:
        cur = conn.execute("DELETE FROM saved_entities WHERE entity_id = ?", (entity_id,))
        deleted = cur.rowcount
        conn.execute(
            "DELETE FROM saved_edges WHERE source_id = ? OR target_id = ?",
            (entity_id, entity_id),
        )
        conn.commit()
    finally:
        conn.close()
    if deleted:
        # A deleted entity must not linger as a search result.
        _enqueue_removal(entity_id)
    return deleted


def get_entities_by_ids(entity_ids: list[str]) -> list[dict]:
    """Fetch many entities in ONE query, preserving the caller's id order.

    Exists so a vector search can hydrate its hits from the system of record in a
    single round trip: the index returns ids + scores, the entities themselves
    always come from SQLite, so a result can never be stale relative to the DB.
    """
    if not entity_ids:
        return []
    conn = get_db()
    try:
        placeholders = ",".join("?" for _ in entity_ids)
        rows = conn.execute(
            f"SELECT * FROM saved_entities WHERE entity_id IN ({placeholders})",
            entity_ids,
        ).fetchall()
    finally:
        conn.close()
    by_id = {r["entity_id"]: row_to_saved_entity(r) for r in rows}
    return [by_id[eid] for eid in entity_ids if eid in by_id]


async def search_entities(
    q: str,
    *,
    entity_type: str | None = None,
    country: str | None = None,
    limit: int = 20,
) -> tuple[list[dict], str]:
    """Semantic+lexical entity search. Returns ``(entities, backend_used)``.

    Deliberately NOT a change to ``list_entities``: that one's ``LIKE`` is a
    substring *filter*, and `graph_list_entities` / `src/routers/discovery.py`
    depend on it behaving exactly that way. This is a *ranker*, and a new
    function, so neither caller changes.

    ``backend_used`` is "hybrid" or "lexical" — callers should surface it rather
    than pretend a fallback was a semantic result.
    """
    try:
        from src.common.vector_index import search_entity_ids

        hits = await search_entity_ids(q, entity_type=entity_type, country=country, top_k=limit)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Entity vector search failed; using lexical: %s", exc)
        hits = None

    if hits is None:  # index unavailable -> today's behaviour, unchanged
        return list_entities(q=q, entity_type=entity_type)[:limit], "lexical"

    entities = get_entities_by_ids([eid for eid, _ in hits])
    if country:
        entities = [e for e in entities if e.get("country") == country]
    return entities[:limit], "hybrid"


# --- Edges ------------------------------------------------------------------


def upsert_edge(
    source_id: str,
    target_id: str,
    relationship_type: str,
    properties: dict | None = None,
    confidence: str = "MEDIUM",
    created_by: str | None = None,
) -> tuple[dict, bool]:
    """Insert or update a relationship, deduped on (source, target, type)."""
    now = _now()
    conn = get_db()
    try:
        existing = conn.execute(
            "SELECT id FROM saved_edges WHERE source_id = ? AND target_id = ? "
            "AND relationship_type = ?",
            (source_id, target_id, relationship_type),
        ).fetchone()
        if existing:
            conn.execute(
                "UPDATE saved_edges SET properties = ?, confidence = ? WHERE id = ?",
                (json.dumps(properties or {}), confidence, existing["id"]),
            )
            edge_id = existing["id"]
            created = False
        else:
            edge_id = _new_id()
            conn.execute(
                "INSERT INTO saved_edges (id, source_id, target_id, relationship_type, "
                "properties, confidence, created_by, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    edge_id,
                    source_id,
                    target_id,
                    relationship_type,
                    json.dumps(properties or {}),
                    confidence,
                    created_by,
                    now,
                ),
            )
            created = True
        conn.commit()
        row = conn.execute("SELECT * FROM saved_edges WHERE id = ?", (edge_id,)).fetchone()
    finally:
        conn.close()
    return row_to_saved_edge(row), created


def list_edges(entity_id: str | None = None) -> list[dict]:
    conn = get_db()
    try:
        if entity_id:
            rows = conn.execute(
                "SELECT * FROM saved_edges WHERE source_id = ? OR target_id = ? "
                "ORDER BY created_at DESC",
                (entity_id, entity_id),
            ).fetchall()
        else:
            rows = conn.execute("SELECT * FROM saved_edges ORDER BY created_at DESC").fetchall()
    finally:
        conn.close()
    return [row_to_saved_edge(r) for r in rows]


def delete_edge(edge_id: str) -> int:
    conn = get_db()
    try:
        cur = conn.execute("DELETE FROM saved_edges WHERE id = ?", (edge_id,))
        deleted = cur.rowcount
        conn.commit()
    finally:
        conn.close()
    return deleted


# --- Traversal --------------------------------------------------------------


def neighbors(entity_id: str) -> list[dict]:
    """Direct neighbors of an entity, with the connecting relationship + direction."""
    out: list[dict] = []
    for ed in list_edges(entity_id):
        if ed["source_id"] == entity_id:
            other, direction = ed["target_id"], "out"
        else:
            other, direction = ed["source_id"], "in"
        ent = get_entity(other)
        out.append(
            {
                "entity_id": other,
                "name": ent["name"] if ent else other,
                "entity_type": ent["entity_type"] if ent else None,
                "relationship_type": ed["relationship_type"],
                "direction": direction,
            }
        )
    return out


def find_paths(source_id: str, target_id: str, max_hops: int = 4) -> list[list[str]]:
    """All simple paths from source to target over the saved graph (treated as
    undirected, since exposure can run either way), capped at ``max_hops`` edges.

    Returns a list of paths, each a list of entity_ids. Empty if either endpoint
    is absent or no path exists within the hop limit.
    """
    import networkx as nx

    edges = list_edges()
    if not edges:
        return []
    g = nx.Graph()
    for ed in edges:
        g.add_edge(ed["source_id"], ed["target_id"])
    if source_id not in g or target_id not in g:
        return []
    try:
        return [list(p) for p in nx.all_simple_paths(g, source_id, target_id, cutoff=max_hops)]
    except nx.NetworkXNoPath:
        return []


def to_vis_graph() -> dict:
    """The whole saved store as a vis.js graph (nodes via the shared factory)."""
    from src.common.graph_helpers import node as _node

    entities = list_entities()
    edges = list_edges()
    ids = {e["entity_id"] for e in entities}
    vis_edges = [
        {
            "from": ed["source_id"],
            "to": ed["target_id"],
            "label": ed["relationship_type"].replace("_", " "),
            "arrows": "to",
            "dashes": False,
        }
        for ed in edges
        if ed["source_id"] in ids and ed["target_id"] in ids
    ]
    # degree-based node sizing (same viz hints as the /api/entity-graph path, #28)
    degree: dict[str, int] = {e["entity_id"]: 0 for e in entities}
    for ve in vis_edges:
        degree[ve["from"]] = degree.get(ve["from"], 0) + 1
        degree[ve["to"]] = degree.get(ve["to"], 0) + 1
    nodes = [
        _node(
            e["entity_id"],
            e["name"],
            e["entity_type"],
            e["country"],
            value=1 + degree.get(e["entity_id"], 0),
        )
        for e in entities
    ]
    by_type: dict[str, int] = {}
    for e in entities:
        by_type[e["entity_type"]] = by_type.get(e["entity_type"], 0) + 1
    return {
        "nodes": nodes,
        "edges": vis_edges,
        "meta": {
            "query": "Saved knowledge graph",
            "summary": {
                "by_type": by_type,
                "sanctioned_count": by_type.get("sanctions_list", 0),
                "high_risk_count": 0,
                "high_risk_entities": [],
            },
            "node_count": len(nodes),
            "edge_count": len(vis_edges),
        },
    }
