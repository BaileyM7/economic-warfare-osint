"""Entity endpoints — graph construction and entity-type resolution.

Extracted from src/api.py (Phase 2 Stage 3). Builds a vis.js entity graph from
GLEIF + OFAC + sector comparables, and classifies free-text queries into
company | person | sector | vessel. Mounted at /api with require_auth applied at
include time (see src/api.py). Behaviour is unchanged from the inline handlers.
"""

from __future__ import annotations

import asyncio
import logging
import re

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from src.common.config import config
from src.common.graph_helpers import canonical_lei as _canonical_lei
from src.common.graph_helpers import node as _node
from src.orchestrator.entity_resolver import resolve_entity_type
from src.sanctions_impact import SANCTIONS_COMPARABLES
from src.tools.corporate.client import (
    gleif_get_direct_parent,
    gleif_get_ultimate_parent,
    gleif_search_lei,
)
from src.tools.sanctions.client import OFACClient, SanctionsClient

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["entity"])


class EntityGraphRequest(BaseModel):
    query: str


class ResolveEntityRequest(BaseModel):
    query: str


# --- Entity Graph endpoint ---


async def _build_entity_graph(query: str) -> tuple[list[dict], list[dict]]:
    """Build entity graph from GLEIF (corporate structure), OFAC (sanctions network),
    and sanctions comparables (sector peers).

    Returns (nodes, edges) in vis.js format.
    """
    nodes: dict[str, dict] = {}
    edges: dict[str, dict] = {}

    def add_node(
        nid: str, name: str, etype: str, country: str | None = None, sayari_id: str | None = None
    ) -> None:
        if nid and name and nid not in nodes:
            nodes[nid] = _node(nid, name, etype, country, sayari_id=sayari_id)

    def add_edge(src: str, tgt: str, label: str, dashes: bool = False, weight: int = 2) -> None:
        if src in nodes and tgt in nodes and src != tgt:
            key = f"{src}→{tgt}→{label}"
            if key not in edges:
                edges[key] = {
                    "from": src,
                    "to": tgt,
                    "label": label.replace("_", " "),
                    "arrows": "to",
                    "dashes": dashes,
                    # `width` drives edge thickness on the frontend (issue #28):
                    # strong corporate ties read heavier than weak/dashed links.
                    "width": weight,
                }

    def slug(s: str) -> str:
        return s.lower().replace(" ", "_").replace(",", "").replace(".", "")[:64]

    # ── 1. GLEIF corporate structure ──────────────────────────────────────
    _all_lei = await gleif_search_lei(query)

    def _graph_lei_matches(q: str, legal_name: str) -> bool:
        q_low, n_low = q.lower(), legal_name.lower()
        q_tok = set(re.findall(r"[a-z0-9]{4,}", q_low))
        if not q_tok:
            return bool(re.search(r"\b" + re.escape(q_low) + r"\b", n_low))
        return bool(q_tok & set(re.findall(r"[a-z0-9]{4,}", n_low)))

    lei_records = [r for r in _all_lei if _graph_lei_matches(query, r.legal_name)]
    main_id = slug(query)
    add_node(main_id, query, "company")

    lei_map: dict[str, str] = {}  # LEI → node id

    for rec in lei_records:
        lei = rec.lei
        name = rec.legal_name
        country = rec.country
        c_lei = _canonical_lei(lei)
        nid = c_lei or slug(name)
        lei_map[lei] = nid
        if c_lei:
            lei_map[c_lei] = nid
        add_node(nid, name, "company", country)
        # Connect to query root if not the same
        if nid != main_id:
            add_edge(main_id, nid, "subsidiary", dashes=False, weight=4)

    # Fetch parent relationships for each LEI
    parent_tasks = []
    for rec in lei_records[:5]:  # limit to avoid slowness
        parent_tasks.append(gleif_get_direct_parent(rec.lei))
        parent_tasks.append(gleif_get_ultimate_parent(rec.lei))

    parent_results = await asyncio.gather(*parent_tasks, return_exceptions=True)
    for result in parent_results:
        if isinstance(result, Exception) or result is None:
            continue
        parent_lei = result.parent_id
        child_lei = result.child_id
        rel_type = result.relationship_type or "parent"

        # Resolve or create parent node
        parent_nid = lei_map.get(parent_lei) or lei_map.get(_canonical_lei(parent_lei))
        child_nid = lei_map.get(child_lei) or lei_map.get(_canonical_lei(child_lei))

        if parent_lei and not parent_nid:
            parent_nid = _canonical_lei(parent_lei) or slug(parent_lei)
            lei_map[parent_lei] = parent_nid
            add_node(parent_nid, f"Parent ({parent_lei[:12]}…)", "company")

        if parent_nid and child_nid:
            add_edge(child_nid, parent_nid, rel_type, weight=4)
        elif parent_nid and main_id:
            add_edge(main_id, parent_nid, rel_type, weight=4)

    # ── 2. OFAC sanctions network ─────────────────────────────────────────
    try:
        ofac = OFACClient()
        ofac_results = await ofac.search(query)
        # Only include high-confidence OFAC matches (score >= 0.85)
        strong_ofac = [e for e in ofac_results if (e.score or 0) >= 0.85]
        for entry in strong_ofac[:10]:
            eid = f"ofac_{slug(entry.name)}"
            add_node(eid, entry.name, "sanctions_list")
            add_edge(main_id, eid, "OFAC SDN", dashes=True, weight=1)

            # Parse "Linked To:" from remarks to build sanctions network
            if entry.remarks and "Linked To:" in entry.remarks:
                import re as _re

                links = _re.findall(r"Linked To:\s*([^;.]+)", entry.remarks)
                for linked_name in links[:3]:
                    linked_name = linked_name.strip().rstrip(".")
                    if linked_name:
                        lid = f"linked_{slug(linked_name)}"
                        add_node(lid, linked_name, "sanctions_list")
                        add_edge(eid, lid, "linked to", weight=1)
    except Exception as exc:
        logger.warning("OFAC graph lookup failed: %s", type(exc).__name__)

    # ── 3. Sector comparable peers ────────────────────────────────────────
    sector_id = f"sector_{slug(query)}"
    add_node(sector_id, "Sanctioned Peers", "sector")
    add_edge(main_id, sector_id, "sector analysis", weight=2)

    for comp in SANCTIONS_COMPARABLES[:8]:
        comp_id = f"comp_{slug(comp['name'])}"
        add_node(comp_id, f"{comp['name']} ({comp['ticker']})", "company")
        add_edge(sector_id, comp_id, "comparable", weight=1)

    # ── 4. Sayari entity resolution (enrich main node with sayariId) ──────
    if config.sayari_client_id and config.sayari_client_secret:
        try:
            from src.tools.sayari.rest_client import get_sayari_client

            sayari = get_sayari_client()
            resolved = await asyncio.wait_for(sayari.resolve(query, limit=1), timeout=10.0)
            if resolved.entities:
                primary = resolved.entities[0]
                if main_id in nodes:
                    nodes[main_id]["sayariId"] = primary.entity_id
        except Exception as exc:
            logger.debug("Sayari resolution skipped for entity graph: %s", exc)

    # ── 5. Sanctions screening of company/person nodes ─────────────────
    # Screen non-sanctions-list entity nodes against OFAC+CSL and recolor
    # sanctioned ones red so the graph visually shows sanctions status.
    screenable = [
        (nid, nd)
        for nid, nd in nodes.items()
        if nd.get("group") in ("company", "person", "vessel")
        and nd.get("group") != "sanctions_list"
    ]
    if screenable:
        sc = SanctionsClient()

        async def _screen(nid: str, nd: dict) -> tuple[str, bool, list[str]]:
            name = nd.get("title", "").split("\n")[0].strip()
            if not name:
                name = nd.get("label", "")
            try:
                status = await asyncio.wait_for(sc.check_status(name), timeout=8.0)
                return nid, status.is_sanctioned, status.programs
            except Exception:
                return nid, False, []

        screen_results = await asyncio.gather(
            *[_screen(nid, nd) for nid, nd in screenable[:20]],
            return_exceptions=True,
        )
        for r in screen_results:
            if isinstance(r, tuple):
                nid, is_sanctioned, programs = r
                if is_sanctioned and nid in nodes:
                    nodes[nid]["color"] = "#F85149"
                    nodes[nid]["riskLevel"] = "HIGH"
                    old_title = nodes[nid].get("title", "")
                    nodes[nid]["title"] = old_title + "\nSANCTIONED"
                    if programs:
                        nodes[nid]["title"] += f" ({', '.join(programs[:3])})"

    # ── 6. Size nodes by connectivity (graph degree) ──────────────────────
    # `value` drives node size via vis-network's nodes.scaling. More-connected
    # entities (and the query root) read as larger / more central. Sanctions-list
    # nodes already carry their own category meaning, so we leave them at base.
    degree: dict[str, int] = {nid: 0 for nid in nodes}
    for e in edges.values():
        degree[e["from"]] = degree.get(e["from"], 0) + 1
        degree[e["to"]] = degree.get(e["to"], 0) + 1
    for nid, nd in nodes.items():
        base = 4 if nid == main_id else 1  # root entity starts larger
        nd["value"] = base + degree.get(nid, 0)

    return list(nodes.values()), list(edges.values())


def _graph_summary(nodes: list[dict], edges: list[dict]) -> dict:
    """A small at-a-glance digest of the graph for the frontend summary panel.

    Pure function over the already-built node/edge dicts (issue #28): counts by
    entity type, how many nodes screened as sanctioned, and the names of the
    high-risk entities so the UI can list them without re-deriving anything.
    """
    by_type: dict[str, int] = {}
    high_risk: list[str] = []
    sanctioned = 0
    for nd in nodes:
        by_type[nd.get("group", "unknown")] = by_type.get(nd.get("group", "unknown"), 0) + 1
        if nd.get("riskLevel") == "HIGH":
            high_risk.append(nd.get("title", nd.get("label", "")).split("\n")[0].strip())
        if nd.get("group") == "sanctions_list" or nd.get("color") == "#F85149":
            sanctioned += 1
    return {
        "by_type": by_type,
        "sanctioned_count": sanctioned,
        "high_risk_count": len(high_risk),
        "high_risk_entities": high_risk[:8],
    }


@router.post("/entity-graph")
async def entity_graph_endpoint(req: EntityGraphRequest):
    """Build vis.js entity graph from GLEIF + OFAC + sector comparables."""
    query = req.query.strip()
    if not query:
        raise HTTPException(status_code=400, detail="Query cannot be empty")
    try:
        graph_nodes, graph_edges = await asyncio.wait_for(
            _build_entity_graph(query),
            timeout=20.0,
        )
        return JSONResponse(
            content={
                "nodes": graph_nodes,
                "edges": graph_edges,
                "meta": {
                    "query": query,
                    "node_count": len(graph_nodes),
                    "edge_count": len(graph_edges),
                    "summary": _graph_summary(graph_nodes, graph_edges),
                },
            }
        )
    except asyncio.TimeoutError:
        return JSONResponse(
            content={
                "nodes": [],
                "edges": [],
                "meta": {
                    "query": query,
                    "node_count": 0,
                    "edge_count": 0,
                    "note": "Data sources timed out",
                },
            }
        )
    except Exception as e:
        logger.exception("Entity graph error for query=%s", query)
        raise HTTPException(status_code=500, detail=str(e))


# --- Entity type resolver ---


@router.post("/resolve-entity")
async def resolve_entity(req: ResolveEntityRequest):
    """Classify a free-text query into company | person | sector | vessel."""
    query = req.query.strip()
    if not query:
        raise HTTPException(status_code=400, detail="Query cannot be empty")
    resolution = await resolve_entity_type(query)
    return {
        "entity_type": resolution.entity_type,
        "entity_name": resolution.entity_name,
        "confidence": resolution.confidence,
        "reasoning": resolution.reasoning,
    }
