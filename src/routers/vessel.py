"""Vessel endpoint — maritime intelligence profile for a vessel.

Extracted from src/api.py (Phase 2 Stage 3). Resolves a query (vessel name,
IMO, or MMSI) to vessel particulars, fetches AIS position history + inferred
port stops, screens the vessel and its owner against OFAC SDN, pulls the
beneficial-ownership chain and trade activity from Sayari, and builds both an
ownership vis.js graph and a separate trade-network graph. Mounted at /api with
require_auth applied at include time (see src/api.py). Behaviour is unchanged
from the original inline handler.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from src.common.graph_helpers import node as _node
from src.llm import generate_narrative as _generate_narrative
from src.llm import generate_recommendations as _generate_recommendations
from src.orchestrator.entity_resolver import resolve_entity_type
from src.tools.sanctions.client import OFACClient
from src.tools.sayari.client import get_vessel_intel
from src.tools.vessels.client import (
    infer_port_stops,
    vessel_by_imo,
    vessel_by_mmsi,
    vessel_find,
    vessel_history,
    vessel_port_calls,
)
from src.tools.vessels.geo import get_countries_from_positions

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["vessel"])


class VesselTrackRequest(BaseModel):
    query: str  # vessel name, IMO, or MMSI
    analyst_question: str = ""


def _build_vessel_sources(vessel_name: str, sayari_intel: Any) -> list[dict[str, Any]]:
    """Construct the structured sources list for a vessel-track response.

    Bare-string sources used to be returned here, which downstream collapsed into
    a single hallucinated rollup label in briefings. Returning structured dicts
    with per-API URLs (and a Sayari record_url when entity_id is known) lets the
    briefing pipeline cite real provenance.
    """
    sources: list[dict[str, Any]] = [
        {
            "name": "OpenSanctions Vessels",
            "url": "https://opensanctions.org/",
            "description": f"Vessel particulars (IMO, MMSI, flag, owner) and sanctioned-vessel flag for {vessel_name}",
        },
        {
            "name": "OFAC SDN",
            "url": "https://sanctionssearch.ofac.treas.gov/",
            "description": f"Sanctions screening against {vessel_name}, owners, and operators",
        },
    ]
    if sayari_intel and sayari_intel.resolved:
        entry: dict[str, Any] = {
            "name": "Sayari Graph",
            "url": "https://app.sayari.com/",
            "description": (
                f"Beneficial ownership chain, registered owner, and trade activity for {vessel_name}"
                + (
                    f" (resolved as {sayari_intel.owner_name})"
                    if getattr(sayari_intel, "owner_name", None)
                    else ""
                )
            ),
        }
        eid = None
        if sayari_intel.ownership and getattr(sayari_intel.ownership, "vessel_entity_id", None):
            eid = sayari_intel.ownership.vessel_entity_id
        if eid:
            entry["record_url"] = f"https://app.sayari.com/entities/{eid}"
        sources.append(entry)
    return sources


@router.post("/vessel-track")
async def vessel_track(req: VesselTrackRequest):
    """Build a vessel intelligence profile: AIS position, route, ownership, sanctions."""
    query = req.query.strip()
    if not query:
        raise HTTPException(status_code=400, detail="Query cannot be empty")

    try:
        # Determine if query looks like MMSI (9 digits), IMO (7 digits or IMO+7digits), or name
        digits_only = query.replace(" ", "").replace("-", "")
        vessel_detail = None
        history = []

        if digits_only.isdigit() and len(digits_only) == 9:
            # AIS positions live in a buffer keyed by MMSI; fetch them in parallel
            # with particulars so the map renders even for vessels that aren't
            # in OpenSanctions / the fixture (buffer-only AIS captures).
            vessel_detail, history = await asyncio.gather(
                vessel_by_mmsi(digits_only),
                vessel_history(digits_only, days=30),
            )
        elif digits_only.upper().startswith("IMO") or (
            digits_only.isdigit() and len(digits_only) == 7
        ):
            imo = digits_only.replace("IMO", "").replace("imo", "")
            vessel_detail = await vessel_by_imo(imo)
            if vessel_detail and vessel_detail.get("mmsi"):
                history = await vessel_history(str(vessel_detail["mmsi"]), days=30)
        else:
            resolution = await resolve_entity_type(query)
            vessel_name = resolution.entity_name
            results = await vessel_find(vessel_name)
            if results:
                vessel_detail = results[0]
                mmsi = vessel_detail.get("mmsi")
                if mmsi:
                    # Fetch full detail (includes current position) + history in parallel
                    full_detail, history = await asyncio.gather(
                        vessel_by_mmsi(str(mmsi)),
                        vessel_history(str(mmsi), days=30),
                    )
                    if full_detail:
                        vessel_detail = full_detail

        if not vessel_detail:
            vessel_detail = {"name": query, "note": "Vessel not found in AIS database"}

        # OFAC + Sayari in parallel
        vessel_name = vessel_detail.get("name", query)
        vessel_imo = vessel_detail.get("imo") or None
        vessel_owner = vessel_detail.get("owner") or None

        async def _ofac_check():
            ofac_client = OFACClient()
            ofac_hits = await ofac_client.search(vessel_name, entity_type="vessel")
            hits = [e for e in ofac_hits if (e.score or 0) >= 0.75]
            if not hits and vessel_owner:
                owner_hits = await ofac_client.search(str(vessel_owner), entity_type="person")
                hits = [e for e in owner_hits if (e.score or 0) >= 0.75][:6]
            return hits

        ofac_task = asyncio.create_task(_ofac_check())
        sayari_task = asyncio.create_task(
            get_vessel_intel(vessel_name, imo=vessel_imo, owner_name=vessel_owner)
        )
        sanctions_hits, sayari_intel = await asyncio.gather(ofac_task, sayari_task)
        is_sanctioned = bool(sanctions_hits)

        # Build vis.js graph: vessel → flag state → operator → sanctions → UBO
        nodes: dict[str, dict] = {}
        edges: dict[str, dict] = {}

        def v_slug(s: str) -> str:
            return s.lower().replace(" ", "_").replace("-", "")[:60]

        vessel_id = f"vessel_{v_slug(vessel_name)}"
        nodes[vessel_id] = _node(vessel_id, vessel_name, "vessel", vessel_detail.get("flag"))

        flag = vessel_detail.get("flag")
        if flag:
            flag_id = f"flag_{v_slug(flag)}"
            nodes[flag_id] = _node(flag_id, f"Flag: {flag}", "government", flag)
            edges[f"{vessel_id}→{flag_id}"] = {
                "from": vessel_id,
                "to": flag_id,
                "label": "flagged under",
                "arrows": "to",
                "dashes": False,
            }

        for entry in sanctions_hits[:4]:
            sid = f"sanc_{v_slug(entry.name)}"
            nodes[sid] = _node(sid, entry.name, "sanctions_list")
            edges[f"{vessel_id}→{sid}"] = {
                "from": vessel_id,
                "to": sid,
                "label": "OFAC match",
                "arrows": "to",
                "dashes": True,
            }

        # Sayari UBO chain → graph nodes (tree structure using parent_entity_id)
        if sayari_intel and sayari_intel.resolved and sayari_intel.ownership:
            # Map Sayari entity IDs → graph node IDs for parent lookups
            entity_to_node: dict[str, str] = {}
            if sayari_intel.ownership.vessel_entity_id:
                entity_to_node[sayari_intel.ownership.vessel_entity_id] = vessel_id

            # Helper to get a human-readable edge label from relationship type
            def _rel_label(rel_type: str, pct: float | None) -> str:
                if pct:
                    return f"owns {pct:.0f}%"
                label_map = {
                    "registered_owner": "registered owner",
                    "owner": "owned by",
                    "beneficial_owner": "beneficial owner",
                    "operator": "operated by",
                    "builder": "built by",
                    "manager": "managed by",
                    "ism_manager": "ISM manager",
                    "charterer": "chartered by",
                    "group_beneficial_owner": "group beneficial owner",
                    "technical_manager": "technical manager",
                    "commercial_manager": "commercial manager",
                }
                return label_map.get(rel_type, rel_type or "beneficial owner")

            for link in sayari_intel.ownership.chain:
                link_id = f"ubo_{v_slug(link.name)}"
                node_type = "person" if link.entity_type == "person" else "company"
                nodes[link_id] = _node(link_id, link.name, node_type, link.country)
                entity_to_node[link.entity_id] = link_id

                # Connect to parent — use parent_entity_id if available, else vessel
                parent_node_id = vessel_id
                if link.parent_entity_id and link.parent_entity_id in entity_to_node:
                    parent_node_id = entity_to_node[link.parent_entity_id]

                edge_label = _rel_label(link.relationship_type, link.ownership_percentage)
                edges[f"{parent_node_id}→{link_id}"] = {
                    "from": parent_node_id,
                    "to": link_id,
                    "label": edge_label,
                    "arrows": "to",
                    "dashes": False,
                }

                if link.is_sanctioned:
                    sanc_id = f"sanc_ubo_{v_slug(link.name)}"
                    nodes[sanc_id] = _node(sanc_id, f"SANCTIONED: {link.name}", "sanctions_list")
                    edges[f"{link_id}→{sanc_id}"] = {
                        "from": link_id,
                        "to": sanc_id,
                        "label": "sanctioned",
                        "arrows": "to",
                        "dashes": True,
                    }

        # Route summary from history
        route_points = [
            {
                "lat": p["latitude"],
                "lon": p["longitude"],
                "speed": p.get("speed", 0),
                "ts": p.get("timestamp", 0),
            }
            for p in history
            if isinstance(p, dict) and "latitude" in p and "longitude" in p
        ]

        # Port calls + countries visited (run concurrently)
        port_calls_data: list[dict] = []
        countries_visited: list[str] = []
        port_stops_inferred: list[dict] = []

        async def _get_port_data():
            nonlocal port_calls_data, countries_visited, port_stops_inferred
            mmsi_str = str(vessel_detail.get("mmsi", ""))
            # Port calls require an AIS position history we don't yet have in
            # Phase 1 of the AISStream migration — returns [] and we fall
            # through to infer_port_stops() on whatever history is available.
            if mmsi_str:
                port_calls_data = await vessel_port_calls(mmsi_str, days=90)
            # Get countries from AIS positions
            if history:
                countries_visited = await get_countries_from_positions(history)
                port_stops_inferred = infer_port_stops(history)
            # If port calls returned countries, add those too
            for pc in port_calls_data:
                c = pc.get("country", "")
                if c and c not in countries_visited:
                    countries_visited.append(c)

        port_data_task = asyncio.create_task(_get_port_data())

        # Start narrative generation concurrently with graph finalization
        compact = {
            "name": vessel_name,
            "imo": vessel_detail.get("imo"),
            "flag": vessel_detail.get("flag"),
            "vessel_type": vessel_detail.get("vessel_type"),
            "owner": vessel_detail.get("owner"),
            "is_sanctioned": is_sanctioned,
            "sanction_programs": [p for e in sanctions_hits for p in (e.programs or [])][:5],
            "route_point_count": len(route_points),
            "has_live_ais": bool(route_points),
        }
        # Enrich with Sayari UBO/trade data for narrative
        if sayari_intel and sayari_intel.resolved:
            compact["beneficial_owners"] = [
                {
                    "name": l.name,
                    "type": l.entity_type,
                    "country": l.country,
                    "sanctioned": l.is_sanctioned,
                    "pep": l.is_pep,
                }
                for l in (sayari_intel.ownership.chain if sayari_intel.ownership else [])  # noqa: E741  # pre-existing; tracked separately
            ]
            compact["trade_countries"] = (
                sayari_intel.trade.trade_countries if sayari_intel.trade else []
            )
            compact["top_commodities"] = [
                h["description"][:50]
                for h in (sayari_intel.trade.top_hs_codes if sayari_intel.trade else [])
            ]
            # Risk scores and trade counterparty data for richer narrative
            compact["ownership_risk_scores"] = sayari_intel.risk_scores
            if sayari_intel.trade:
                # Collect unique trade counterparties and their risk flags
                counterparties = set()
                risk_flags = set()
                for r in sayari_intel.trade.records:
                    if r.supplier:
                        counterparties.add(r.supplier)
                    if r.buyer:
                        counterparties.add(r.buyer)
                    risk_flags.update(r.supplier_risks)
                    risk_flags.update(r.buyer_risks)
                compact["trade_counterparty_count"] = len(counterparties)
                compact["trade_counterparty_risk_flags"] = list(risk_flags)[:10]
        # Wait for port data before building narrative
        await port_data_task

        compact["countries_visited"] = countries_visited
        compact["port_calls"] = [
            {"port": pc.get("port_name"), "country": pc.get("country")}
            for pc in port_calls_data[:10]
        ]

        vessel_prompt = (
            f"You are a senior maritime intelligence analyst briefing a decision-maker. "
            f"Given the following vessel intelligence for {vessel_name}, write 3-5 sentences "
            f"delivering a definitive risk characterization. State conclusions with authority — "
            f"do NOT hedge, qualify with 'limited data', or say 'warrants further investigation'. "
            f"If the vessel is clean, state it is clean and why. If there are red flags, state "
            f"exactly what the risk is. Cover: sanctions status, flag-of-convenience indicators, "
            f"beneficial ownership chain, trade patterns, commodity flows, and dark shipping indicators. "
            f"Reference specific entity names, countries, and risk scores from the data.\n"
            f"Data: {json.dumps(compact)}"
        )
        narrative_task = asyncio.create_task(_generate_narrative(vessel_prompt))
        coa_task = asyncio.create_task(_generate_recommendations(compact, req.analyst_question))

        graph_result = {
            "nodes": list(nodes.values()),
            "edges": list(edges.values()),
        }

        # Build trade network graph (separate from ownership graph)
        trade_nodes: dict[str, dict] = {}
        trade_edges: dict[str, dict] = {}
        if sayari_intel and sayari_intel.trade and sayari_intel.trade.records:
            # Include vessel node as anchor
            trade_nodes[vessel_id] = nodes[vessel_id]
            seen_companies: set[str] = set()
            for rec in sayari_intel.trade.records:
                for role, company_name, risks in [
                    ("supplier", rec.supplier, rec.supplier_risks),
                    ("buyer", rec.buyer, rec.buyer_risks),
                ]:
                    if not company_name or company_name in seen_companies:
                        continue
                    seen_companies.add(company_name)
                    cid = f"trade_{v_slug(company_name)}"
                    has_risk = bool(risks)
                    node_type = "sanctions_list" if has_risk else "company"
                    trade_nodes[cid] = _node(cid, company_name, node_type)
                    cat = rec.commodity_category or "goods"
                    trade_edges[f"{vessel_id}→{cid}"] = {
                        "from": vessel_id,
                        "to": cid,
                        "label": f"{role}: {cat}",
                        "arrows": "to",
                        "dashes": has_risk,
                    }

        trade_graph_result = {
            "nodes": list(trade_nodes.values()),
            "edges": list(trade_edges.values()),
        }

        narrative, recommendations = await asyncio.gather(narrative_task, coa_task)

        return JSONResponse(
            content={
                "vessel": vessel_detail,
                "is_sanctioned": is_sanctioned,
                "sanctions_matches": [
                    {"name": e.name, "score": e.score, "programs": e.programs or []}
                    for e in sanctions_hits
                ],
                "route_history": route_points,
                "countries_visited": countries_visited,
                "port_calls": port_calls_data,
                "port_stops_inferred": port_stops_inferred,
                "ownership_chain": (
                    [link.model_dump() for link in sayari_intel.ownership.chain]
                    if sayari_intel and sayari_intel.ownership
                    else []
                ),
                "owner_name": sayari_intel.owner_name if sayari_intel else None,
                "trade_activity": (
                    sayari_intel.trade.model_dump() if sayari_intel and sayari_intel.trade else None
                ),
                "risk_scores": sayari_intel.risk_scores if sayari_intel else {},
                "graph": graph_result,
                "trade_graph": trade_graph_result,
                "narrative": narrative,
                "recommendations": recommendations,
                "sources": _build_vessel_sources(vessel_name, sayari_intel),
            }
        )

    except Exception as e:
        logger.exception("vessel_track error for query=%s", query)
        raise HTTPException(status_code=500, detail=str(e))
