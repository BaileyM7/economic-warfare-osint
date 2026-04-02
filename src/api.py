"""FastAPI web server for the Economic Warfare OSINT system.

Run with:
    uv run uvicorn src.api:app --reload --host 0.0.0.0 --port 8000
"""

from __future__ import annotations

import asyncio
import json
import re
import uuid
import webbrowser
from datetime import date
from typing import Any

import anthropic
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import logging

from src.common.config import config
from src.fusion.renderer import render_entity_graph
from src.orchestrator.entity_resolver import resolve_entity_type
from src.orchestrator.main import Orchestrator
from src.orchestrator.tool_registry import ToolRegistry
from src.sanctions_impact import run_sanctions_impact, SANCTIONS_COMPARABLES
from src.tools.corporate.client import (
    gleif_search_lei,
    gleif_get_direct_parent,
    gleif_get_ultimate_parent,
    oc_search_officers,
    oc_search_companies,
    icij_search,
)
from src.tools.market.client import YFinanceClient, _is_pension_or_sovereign
from src.tools.geopolitical.client import refresh_acled_token, gdelt_doc_search
from src.tools.geopolitical.server import get_bilateral_tensions
from src.tools.sanctions.client import OFACClient, OpenSanctionsClient, SanctionsClient
from src.tools.screening.client import search_csl
from src.tools.trade.server import get_supply_chain_exposure
from src.tools.vessels.client import vessel_find, vessel_by_mmsi, vessel_by_imo, vessel_history, vessel_port_calls, infer_port_stops
from src.tools.vessels.geo import get_countries_from_positions
from src.tools.sayari.client import get_vessel_intel

logger = logging.getLogger(__name__)

_anthropic_client: anthropic.AsyncAnthropic | None = None


def _ofac_hit_matches_company_label(company_name: str, entry: Any) -> bool:
    """Require a significant token from *company_name* to appear as a whole token in the OFAC row.

    This filters substring false positives (e.g. "intel" matching "intelligence",
    "samsung" matching "SAMSUN", short ticker tokens matching unrelated words).
    """
    tokens = [t for t in re.findall(r"[a-z0-9]+", company_name.lower()) if len(t) >= 2]
    if not tokens:
        return False
    significant = [t for t in tokens if len(t) >= 4] or tokens
    rows: list[str] = [getattr(entry, "name", "") or ""]
    aliases = getattr(entry, "aliases", None) or []
    rows.extend(str(a) for a in aliases if a)
    for text in rows:
        row_tokens = set(re.findall(r"[a-z0-9]+", text.lower()))
        for t in significant:
            if t in row_tokens:
                return True
    return False


def _get_anthropic_client() -> anthropic.AsyncAnthropic | None:
    global _anthropic_client
    if _anthropic_client is None and config.anthropic_api_key:
        _anthropic_client = anthropic.AsyncAnthropic(api_key=config.anthropic_api_key)
    return _anthropic_client


async def _generate_narrative(prompt: str) -> str:
    """Generate a 3–5 sentence analyst narrative. Returns '' on any failure."""
    client = _get_anthropic_client()
    if not client:
        return ""
    try:
        response = await asyncio.wait_for(
            client.messages.create(
                model=config.model,
                max_tokens=500,
                messages=[{"role": "user", "content": prompt}],
            ),
            timeout=15.0,
        )
        return response.content[0].text.strip()
    except Exception as exc:
        logger.warning("narrative generation failed: %s", exc)
        return ""


app = FastAPI(
    title="Economic Warfare OSINT",
    description="Multi-agent OSINT system for economic warfare scenario analysis",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

_DIST = Path(__file__).parent.parent / "frontend" / "dist"
if (_DIST / "assets").exists():
    app.mount("/assets", StaticFiles(directory=str(_DIST / "assets")), name="assets")

_browser_opened = False


@app.on_event("startup")
async def _startup() -> None:
    global _browser_opened
    await refresh_acled_token()
    if not _browser_opened:
        _browser_opened = True
        webbrowser.open("http://localhost:8000")


# --- In-memory state for async orchestrator analyses ---
_analyses: dict[str, dict[str, Any]] = {}


# --- Request / Response models ---

class SanctionsImpactRequest(BaseModel):
    ticker: str


class EntityGraphRequest(BaseModel):
    query: str


class PersonProfileRequest(BaseModel):
    name: str


class VesselTrackRequest(BaseModel):
    query: str  # vessel name, IMO, or MMSI


class SectorAnalysisRequest(BaseModel):
    sector: str


class AnalyzeRequest(BaseModel):
    query: str


class AnalyzeResponse(BaseModel):
    analysis_id: str
    status: str


class AnalysisStatus(BaseModel):
    analysis_id: str
    status: str
    progress: list[str]
    result: dict[str, Any] | None = None
    markdown: str | None = None
    graph_data: dict[str, Any] | None = None
    error: str | None = None


# --- Health / info ---

@app.get("/")
async def root():
    index = _DIST / "index.html"
    if index.exists():
        return FileResponse(str(index))
    return HTMLResponse(content=_read_index_html())


@app.get("/api/health")
async def health():
    issues = config.validate()
    return {
        "status": "ok" if not issues else "misconfigured",
        "issues": issues,
        "model": config.model,
        "tools_available": True,
    }


@app.get("/api/tools")
async def list_tools():
    registry = ToolRegistry()
    await registry._ensure_loaded()
    return {"tools": registry.list_tools()}


async def _run_analysis(analysis_id: str, query: str) -> None:
    def on_progress(msg: str) -> None:
        _analyses[analysis_id]["progress"].append(msg)

    on_progress("Starting analysis pipeline...")
    try:
        orchestrator = Orchestrator()
        assessment = await orchestrator.analyze(query, progress_callback=on_progress)
        _analyses[analysis_id]["result"] = assessment.model_dump(mode="json")
        _analyses[analysis_id]["status"] = "completed"
        on_progress("Done.")
    except Exception as e:
        _analyses[analysis_id]["status"] = "failed"
        _analyses[analysis_id]["error"] = str(e)
        on_progress(f"Error: {e}")


@app.post("/api/analyze", response_model=AnalyzeResponse)
async def start_analysis(req: AnalyzeRequest):
    query = (req.query or "").strip()
    if not query:
        raise HTTPException(status_code=400, detail="Query cannot be empty")

    analysis_id = uuid.uuid4().hex[:8]
    _analyses[analysis_id] = {
        "analysis_id": analysis_id,
        "status": "running",
        "progress": ["Queued"],
        "result": None,
        "markdown": None,
        "graph_data": None,
        "error": None,
    }
    asyncio.create_task(_run_analysis(analysis_id, query))
    return {"analysis_id": analysis_id, "status": "running"}


@app.get("/api/analyze/{analysis_id}", response_model=AnalysisStatus)
async def get_analysis(analysis_id: str):
    status = _analyses.get(analysis_id)
    if not status:
        raise HTTPException(status_code=404, detail="Analysis not found")
    return status


@app.post("/api/analyze/sync")
async def analyze_sync(req: AnalyzeRequest):
    query = (req.query or "").strip()
    if not query:
        raise HTTPException(status_code=400, detail="Query cannot be empty")

    orchestrator = Orchestrator()
    assessment = await orchestrator.analyze(query)
    return JSONResponse(content=assessment.model_dump(mode="json"))


# --- Sanctions Impact Projector endpoint ---

@app.post("/api/sanctions-impact")
async def sanctions_impact(req: SanctionsImpactRequest):
    """Project stock price impact from sanctions based on historical comparables."""
    ticker = req.ticker.strip().upper()
    if not ticker:
        raise HTTPException(status_code=400, detail="Ticker cannot be empty")

    try:
        result = await run_sanctions_impact(ticker)

        # Build narrative prompt from result data
        target = result.get("target", {})
        summary = result.get("projection", {}).get("summary", {})
        comp_count = result.get("metadata", {}).get("comparable_count", 0)
        is_sanctioned = target.get("sanctions_status", {}).get("is_sanctioned", False)
        programs = target.get("sanctions_status", {}).get("programs", [])
        compact = {
            "name": target.get("name", ticker),
            "ticker": ticker,
            "sector": target.get("sector"),
            "country": target.get("country"),
            "is_sanctioned": is_sanctioned,
            "sanction_programs": programs,
            "pre_event_decline_pct": summary.get("pre_event_decline"),
            "day_30_post_pct": summary.get("day_30_post"),
            "day_90_post_pct": summary.get("day_90_post"),
            "max_drawdown_pct": summary.get("max_drawdown"),
        }
        prompt = (
            f"You are an economic warfare analyst. Given the following data about "
            f"{compact['name']} ({ticker}), write a 3-5 sentence risk narrative covering: "
            f"(1) current sanctions status, (2) likely stock price trajectory — note the "
            f"projection uses market-adjusted excess returns vs the sector ETF benchmark, so "
            f"values reflect underperformance vs the sector, not necessarily absolute price "
            f"declines; translate to plain language for the reader, (3) key supply chain or "
            f"investor exposure that constitutes friendly fire risk.\n"
            f"Data: {json.dumps(compact)}\n"
            f"Confidence qualifier: {comp_count} comparable cases used, data as of "
            f"{date.today().isoformat()}."
        )
        narrative = await _generate_narrative(prompt)
        result["narrative"] = narrative
        return JSONResponse(content=result)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# --- Entity Graph endpoint ---

_ENTITY_COLORS: dict[str, str] = {
    "company": "#58a6ff", "person": "#a371f7", "government": "#DC143C",
    "vessel": "#3fb950", "sanctions_list": "#F85149",
    "theme": "#F0883E", "sector": "#f0883e",
}


def _truncate(s: str, n: int = 28) -> str:
    return s if len(s) <= n else s[:n - 1] + "…"


def _node(nid: str, name: str, entity_type: str, country: str | None = None, sayari_id: str | None = None) -> dict[str, Any]:
    title = f"{name}\n{entity_type}" + (f" · {country}" if country else "")
    node: dict[str, Any] = {"id": nid, "label": _truncate(name), "title": title,
            "group": entity_type, "color": _ENTITY_COLORS.get(entity_type, "#808080")}
    if sayari_id:
        node["sayariId"] = sayari_id
    return node


_LEI_20 = re.compile(r"[A-Z0-9]{20}")


def _canonical_lei(ref: str | None) -> str:
    """Extract a 20-character LEI from a bare code or JSON:API href-style id."""
    if not ref or not isinstance(ref, str):
        return ""
    compact = ref.strip().upper().replace("-", "").replace(" ", "")
    m = _LEI_20.search(compact)
    return m.group(0) if m else ""


def _lei_resolve_node_id(lei_map: dict[str, str], ref: str | None) -> str | None:
    """Map a parent/child reference from API payloads to our graph node id."""
    if ref is None or ref == "":
        return None
    raw = str(ref).strip()
    cand = _canonical_lei(raw)
    for key in (raw, cand):
        if key and key in lei_map:
            return lei_map[key]
    return None


async def _build_entity_graph(query: str) -> tuple[list[dict], list[dict]]:
    """Build entity graph from GLEIF (corporate structure), OFAC (sanctions network),
    and sanctions comparables (sector peers).

    Returns (nodes, edges) in vis.js format.
    """
    nodes: dict[str, dict] = {}
    edges: dict[str, dict] = {}

    def add_node(nid: str, name: str, etype: str, country: str | None = None, sayari_id: str | None = None) -> None:
        if nid and name and nid not in nodes:
            nodes[nid] = _node(nid, name, etype, country, sayari_id=sayari_id)

    def add_edge(src: str, tgt: str, label: str, dashes: bool = False) -> None:
        if src in nodes and tgt in nodes and src != tgt:
            key = f"{src}→{tgt}→{label}"
            if key not in edges:
                edges[key] = {"from": src, "to": tgt,
                              "label": label.replace("_", " "), "arrows": "to", "dashes": dashes}

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
            add_edge(main_id, nid, "subsidiary", dashes=False)

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
            add_edge(child_nid, parent_nid, rel_type)
        elif parent_nid and main_id:
            add_edge(main_id, parent_nid, rel_type)

    # ── 2. OFAC sanctions network ─────────────────────────────────────────
    try:
        ofac = OFACClient()
        ofac_results = await ofac.search(query)
        # Only include high-confidence OFAC matches (score >= 0.85)
        strong_ofac = [e for e in ofac_results if (e.score or 0) >= 0.85]
        for entry in strong_ofac[:10]:
            eid = f"ofac_{slug(entry.name)}"
            add_node(eid, entry.name, "sanctions_list")
            add_edge(main_id, eid, "OFAC SDN", dashes=True)

            # Parse "Linked To:" from remarks to build sanctions network
            if entry.remarks and "Linked To:" in entry.remarks:
                import re as _re
                links = _re.findall(r"Linked To:\s*([^;.]+)", entry.remarks)
                for linked_name in links[:3]:
                    linked_name = linked_name.strip().rstrip(".")
                    if linked_name:
                        lid = f"linked_{slug(linked_name)}"
                        add_node(lid, linked_name, "sanctions_list")
                        add_edge(eid, lid, "linked to")
    except Exception as exc:
        logger.warning("OFAC graph lookup failed: %s", type(exc).__name__)

    # ── 3. Sector comparable peers ────────────────────────────────────────
    sector_id = f"sector_{slug(query)}"
    add_node(sector_id, "Sanctioned Peers", "sector")
    add_edge(main_id, sector_id, "sector analysis")

    for comp in SANCTIONS_COMPARABLES[:8]:
        comp_id = f"comp_{slug(comp['name'])}"
        add_node(comp_id, f"{comp['name']} ({comp['ticker']})", "company")
        add_edge(sector_id, comp_id, "comparable")

    # ── 4. Sayari entity resolution (enrich main node with sayariId) ──────
    if config.sayari_client_id and config.sayari_client_secret:
        try:
            from src.tools.sayari.client import get_sayari_client
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
        (nid, nd) for nid, nd in nodes.items()
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
                    old_title = nodes[nid].get("title", "")
                    nodes[nid]["title"] = old_title + "\nSANCTIONED"
                    if programs:
                        nodes[nid]["title"] += f" ({', '.join(programs[:3])})"

    return list(nodes.values()), list(edges.values())


@app.post("/api/entity-graph")
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
        return JSONResponse(content={
            "nodes": graph_nodes,
            "edges": graph_edges,
            "meta": {"query": query, "node_count": len(graph_nodes), "edge_count": len(graph_edges)},
        })
    except asyncio.TimeoutError:
        return JSONResponse(content={
            "nodes": [], "edges": [],
            "meta": {"query": query, "node_count": 0, "edge_count": 0, "note": "Data sources timed out"},
        })
    except Exception as e:
        logger.exception("Entity graph error for query=%s", query)
        raise HTTPException(status_code=500, detail=str(e))


# --- Sayari endpoints ---

class SayariResolveRequest(BaseModel):
    query: str
    limit: int = 5
    entity_type: str | None = None

class SayariEntityRequest(BaseModel):
    entity_id: str

class SayariTraversalRequest(BaseModel):
    entity_id: str
    depth: int = 1
    limit: int = 20


@app.post("/api/sayari/resolve")
async def sayari_resolve_endpoint(req: SayariResolveRequest):
    """Resolve a name to Sayari entity IDs."""
    from src.tools.sayari.client import get_sayari_client
    try:
        client = get_sayari_client()
        result = await asyncio.wait_for(
            client.resolve(req.query.strip(), limit=req.limit, entity_type=req.entity_type),
            timeout=15.0,
        )
        return JSONResponse(content=result.model_dump(mode="json"))
    except Exception as e:
        logger.warning("Sayari resolve error: %s", e)
        raise HTTPException(status_code=502, detail=str(e))


@app.post("/api/sayari/related")
async def sayari_related_endpoint(req: SayariTraversalRequest):
    """Get entities related to a Sayari entity (graph traversal)."""
    from src.tools.sayari.client import get_sayari_client
    try:
        client = get_sayari_client()
        result = await asyncio.wait_for(
            client.get_traversal(req.entity_id, depth=req.depth, limit=req.limit),
            timeout=20.0,
        )
        return JSONResponse(content=result.model_dump(mode="json"))
    except Exception as e:
        logger.warning("Sayari traversal error: %s", e)
        raise HTTPException(status_code=502, detail=str(e))


@app.post("/api/sayari/ubo")
async def sayari_ubo_endpoint(req: SayariEntityRequest):
    """Get ultimate beneficial owners of a Sayari entity."""
    from src.tools.sayari.client import get_sayari_client
    try:
        client = get_sayari_client()
        result = await asyncio.wait_for(
            client.get_ubo(req.entity_id),
            timeout=20.0,
        )
        return JSONResponse(content=result.model_dump(mode="json"))
    except Exception as e:
        logger.warning("Sayari UBO error: %s", e)
        raise HTTPException(status_code=502, detail=str(e))


# --- Batch sanctions screening ---

class SanctionsScreenBatchRequest(BaseModel):
    names: list[str]


@app.post("/api/sanctions/screen-batch")
async def sanctions_screen_batch(req: SanctionsScreenBatchRequest):
    """Screen a list of entity names against OFAC SDN + Trade.gov CSL.

    Returns a dict keyed by the input name with sanctions status for each.
    Runs all checks concurrently; individual failures are reported as not-sanctioned.
    """
    names = [n.strip() for n in req.names if n.strip()][:30]
    if not names:
        return JSONResponse(content={"results": {}})

    client = SanctionsClient()

    async def _check_one(name: str) -> tuple[str, dict]:
        try:
            status = await asyncio.wait_for(client.check_status(name), timeout=10.0)
            return name, {
                "sanctioned": status.is_sanctioned,
                "lists": status.lists_found,
                "programs": status.programs,
            }
        except Exception:
            return name, {"sanctioned": False, "lists": [], "programs": []}

    results = await asyncio.gather(*[_check_one(n) for n in names], return_exceptions=True)
    output: dict[str, dict] = {}
    for r in results:
        if isinstance(r, tuple):
            output[r[0]] = r[1]
    return JSONResponse(content={"results": output})


# --- Entity type resolver ---

@app.post("/api/resolve-entity")
async def resolve_entity(req: AnalyzeRequest):
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


# --- Person Profile endpoint ---

@app.post("/api/person-profile")
async def person_profile(req: PersonProfileRequest):
    """Build an insider-threat style profile for a named individual.

    Aggregates: OpenSanctions (person schema) · OFAC SDN · corporate
    affiliations (OpenCorporates officers) · ICIJ offshore connections ·
    GDELT recent news events.
    """
    name = req.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="Name cannot be empty")

    try:
        # Run all lookups concurrently
        sanctions_client = SanctionsClient()
        ofac_client = sanctions_client.ofac

        csl_task = asyncio.create_task(search_csl(name))
        ofac_task = asyncio.create_task(ofac_client.search(name, entity_type="person"))
        officers_task = asyncio.create_task(oc_search_officers(name))
        icij_task = asyncio.create_task(icij_search(name, entity_type="officer"))
        gdelt_task = asyncio.create_task(gdelt_doc_search(name, days=30))

        (
            csl_hits_raw,
            ofac_hits,
            officer_records,
            icij_hits,
            gdelt_events,
        ) = await asyncio.gather(
            csl_task, ofac_task, officers_task, icij_task, gdelt_task,
            return_exceptions=True,
        )

        def _safe(result, default):
            return default if isinstance(result, Exception) else result

        csl_hits_raw = _safe(csl_hits_raw, [])
        ofac_hits = _safe(ofac_hits, [])
        officer_records = _safe(officer_records, [])
        icij_hits = _safe(icij_hits, [])
        gdelt_events = _safe(gdelt_events, {})

        sanctions_hits = sanctions_client._csl_to_entries(csl_hits_raw or [])

        # Build sanctions summary
        is_sanctioned = bool(
          [e for e in sanctions_hits if (e.score or 0) >= 0.6]
          or [e for e in ofac_hits if (e.score or 0) >= 0.7]
        )
        sanction_programs: list[str] = []
        for e in ofac_hits:
            if (e.score or 0) >= 0.7 and e.programs:
                sanction_programs.extend(e.programs)
        for e in sanctions_hits:
          if (e.score or 0) >= 0.6 and e.programs:
            sanction_programs.extend(e.programs)
        sanction_programs = list(set(sanction_programs))[:5]

        # Best match for bio data
        best_match = next(
          (e for e in sanctions_hits if (e.score or 0) >= 0.6),
            sanctions_hits[0] if sanctions_hits else None,
        )
        aliases = best_match.aliases if best_match else []
        # Nationality/DOB may appear in identifiers or remarks
        nationality = (best_match.identifiers.get("nationality") or
                       best_match.identifiers.get("citizenship")) if best_match else None
        dob = best_match.identifiers.get("dob") if best_match else None

        # Corporate affiliations — officer_records are Officer objects
        affiliations = []
        for off in (officer_records or [])[:12]:
            is_active = off.end_date is None if hasattr(off, "end_date") else True
            affiliations.append({
                "company": off.name,
                "role": off.role,
                "nationality": off.nationality or "",
                "active": is_active,
            })

        # ICIJ connections
        offshore = []
        for h in (icij_hits or [])[:5]:
            offshore.append({
                "entity": h.name,
                "dataset": h.source_dataset or "",
                "jurisdiction": h.jurisdiction or "",
            })

        # Recent events from GDELT (list[GdeltEvent])
        recent_events = []
        if isinstance(gdelt_events, list):
            for ev in gdelt_events[:8]:
                recent_events.append({
                    "title": ev.event_id[:80] if hasattr(ev, "event_id") else str(ev),
                    "date": ev.date.isoformat() if hasattr(ev, "date") and ev.date else "",
                    "source": ev.source_url if hasattr(ev, "source_url") else "",
                    "tone": ev.avg_tone if hasattr(ev, "avg_tone") else None,
                })

        # Build person-centric vis.js graph
        nodes: dict[str, dict] = {}
        edges: dict[str, dict] = {}

        def p_slug(s: str) -> str:
            return s.lower().replace(" ", "_").replace(",", "")[:60]

        person_id = f"person_{p_slug(name)}"
        nodes[person_id] = _node(person_id, name, "person")

        for e in [e for e in sanctions_hits if (e.score or 0) >= 0.6][:6]:
            eid = f"sanc_{p_slug(e.name)}"
            nodes[eid] = _node(eid, e.name, "sanctions_list")
            key = f"{person_id}→{eid}"
            edges[key] = {"from": person_id, "to": eid, "label": "OFAC/OS match",
                          "arrows": "to", "dashes": True}

        # OFAC-only rows (OpenSanctions may be empty without API key)
        for e in [e for e in ofac_hits if (e.score or 0) >= 0.7][:6]:
            slug = getattr(e, "id", None) or p_slug(e.name)
            eid = f"ofac_{p_slug(str(slug))}"
            if eid in nodes:
                continue
            nodes[eid] = _node(eid, e.name, "sanctions_list")
            key = f"{person_id}→{eid}"
            edges[key] = {
                "from": person_id, "to": eid, "label": "OFAC SDN",
                "arrows": "to", "dashes": True,
            }

        for aff in affiliations[:8]:
            cid = f"co_{p_slug(aff['company'])}"
            nodes[cid] = _node(cid, aff["company"], "company")
            key = f"{person_id}→{cid}"
            edges[key] = {"from": person_id, "to": cid,
                          "label": aff.get("role", "officer"),
                          "arrows": "to", "dashes": False}

        for off in offshore[:4]:
            oid = f"offshore_{p_slug(off['entity'])}"
            nodes[oid] = _node(oid, off["entity"], "theme",
                               off.get("jurisdiction"))
            key = f"{person_id}→{oid}"
            edges[key] = {"from": person_id, "to": oid,
                          "label": "offshore", "arrows": "to", "dashes": True}

        # Start narrative generation concurrently with graph finalization
        compact = {
            "name": name,
            "is_sanctioned": is_sanctioned,
            "sanction_programs": sanction_programs,
            "nationality": nationality,
            "affiliation_count": len(affiliations),
            "affiliations_preview": [
                {"company": a["company"], "role": a["role"]} for a in affiliations[:4]
            ],
            "offshore_connection_count": len(offshore),
            "recent_event_count": len(recent_events),
            "sources_searched": ["OpenSanctions", "OFAC SDN", "OpenCorporates",
                                  "ICIJ Offshore Leaks", "GDELT"],
        }
        person_prompt = (
            f"You are an economic warfare analyst writing a due diligence summary for {name}. "
            f"Sources searched: OpenSanctions, OFAC SDN, OpenCorporates (corporate affiliations), "
            f"ICIJ Offshore Leaks, GDELT (recent news). Data as of {date.today().isoformat()}.\n"
            f"Findings: {json.dumps(compact)}\n"
            f"Write 3-5 sentences characterizing this individual's risk profile. "
            f"If no derogatory findings were found, state that clearly and note what the "
            f"clean profile means analytically."
        )
        narrative_task = asyncio.create_task(_generate_narrative(person_prompt))

        graph_result = {
            "nodes": list(nodes.values()),
            "edges": list(edges.values()),
        }
        narrative = await narrative_task

        return JSONResponse(content={
            "name": name,
            "is_sanctioned": is_sanctioned,
            "sanction_programs": sanction_programs,
            "aliases": aliases[:6],
            "nationality": nationality,
            "dob": str(dob) if dob else None,
            "affiliations": affiliations,
            "offshore_connections": offshore,
            "recent_events": recent_events,
            "graph": graph_result,
            "narrative": narrative,
            "sources": ["OpenSanctions", "OFAC SDN", "OpenCorporates", "ICIJ Offshore Leaks", "GDELT"],
        })

    except Exception as e:
        logger.exception("person_profile error for name=%s", name)
        raise HTTPException(status_code=500, detail=str(e))


# --- Vessel Track endpoint ---

@app.post("/api/vessel-track")
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
            vessel_detail = await vessel_by_mmsi(digits_only)
            if vessel_detail:
                history = await vessel_history(digits_only, days=30)
        elif digits_only.upper().startswith("IMO") or (digits_only.isdigit() and len(digits_only) == 7):
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
        nodes[vessel_id] = _node(vessel_id, vessel_name, "vessel",
                                 vessel_detail.get("flag"))

        flag = vessel_detail.get("flag")
        if flag:
            flag_id = f"flag_{v_slug(flag)}"
            nodes[flag_id] = _node(flag_id, f"Flag: {flag}", "government", flag)
            edges[f"{vessel_id}→{flag_id}"] = {
                "from": vessel_id, "to": flag_id, "label": "flagged under", "arrows": "to", "dashes": False,
            }

        for entry in sanctions_hits[:4]:
            sid = f"sanc_{v_slug(entry.name)}"
            nodes[sid] = _node(sid, entry.name, "sanctions_list")
            edges[f"{vessel_id}→{sid}"] = {
                "from": vessel_id, "to": sid, "label": "OFAC match", "arrows": "to", "dashes": True,
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
                    "from": parent_node_id, "to": link_id, "label": edge_label,
                    "arrows": "to", "dashes": False,
                }

                if link.is_sanctioned:
                    sanc_id = f"sanc_ubo_{v_slug(link.name)}"
                    nodes[sanc_id] = _node(sanc_id, f"SANCTIONED: {link.name}", "sanctions_list")
                    edges[f"{link_id}→{sanc_id}"] = {
                        "from": link_id, "to": sanc_id, "label": "sanctioned",
                        "arrows": "to", "dashes": True,
                    }

        # Route summary from history
        route_points = [
            {"lat": p["latitude"], "lon": p["longitude"],
             "speed": p.get("speed", 0), "ts": p.get("timestamp", 0)}
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
            # Try Datalastic port call API first
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
            "sanction_programs": [
                p for e in sanctions_hits for p in (e.programs or [])
            ][:5],
            "route_point_count": len(route_points),
            "has_live_ais": bool(route_points),
        }
        # Enrich with Sayari UBO/trade data for narrative
        if sayari_intel and sayari_intel.resolved:
            compact["beneficial_owners"] = [
                {"name": l.name, "type": l.entity_type, "country": l.country,
                 "sanctioned": l.is_sanctioned, "pep": l.is_pep}
                for l in (sayari_intel.ownership.chain if sayari_intel.ownership else [])
            ]
            compact["trade_countries"] = sayari_intel.trade.trade_countries if sayari_intel.trade else []
            compact["top_commodities"] = [
                h["description"][:50] for h in (sayari_intel.trade.top_hs_codes if sayari_intel.trade else [])
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
            f"You are a maritime intelligence analyst. Given the following vessel intelligence "
            f"for {vessel_name}, write 3-5 sentences characterizing the risk profile: "
            f"sanctions status, flag-of-convenience indicators, beneficial ownership chain, "
            f"trade patterns, countries visited, and any dark shipping indicators.\n"
            f"Data: {json.dumps(compact)}"
        )
        narrative_task = asyncio.create_task(_generate_narrative(vessel_prompt))

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
                        "from": vessel_id, "to": cid,
                        "label": f"{role}: {cat}",
                        "arrows": "to", "dashes": has_risk,
                    }

        trade_graph_result = {
            "nodes": list(trade_nodes.values()),
            "edges": list(trade_edges.values()),
        }

        narrative = await narrative_task

        return JSONResponse(content={
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
                if sayari_intel and sayari_intel.ownership else []
            ),
            "owner_name": sayari_intel.owner_name if sayari_intel else None,
            "trade_activity": (
                sayari_intel.trade.model_dump() if sayari_intel and sayari_intel.trade else None
            ),
            "risk_scores": sayari_intel.risk_scores if sayari_intel else {},
            "graph": graph_result,
            "trade_graph": trade_graph_result,
            "narrative": narrative,
            "sources": ["Datalastic AIS", "OFAC SDN"] + (
                ["Sayari Graph"] if sayari_intel and sayari_intel.resolved else []
            ),
        })

    except Exception as e:
        logger.exception("vessel_track error for query=%s", query)
        raise HTTPException(status_code=500, detail=str(e))


# --- Sector Analysis endpoint ---

_SECTOR_COMPANIES: dict[str, list[dict]] = {
    "semiconductor": [
        {"name": "TSMC", "ticker": "TSM", "country": "TW"},
        {"name": "Samsung Electronics", "ticker": "005930.KS", "country": "KR"},
        {"name": "ASML", "ticker": "ASML", "country": "NL"},
        {"name": "Nvidia", "ticker": "NVDA", "country": "US"},
        {"name": "Intel", "ticker": "INTC", "country": "US"},
        {"name": "SMIC", "ticker": "0981.HK", "country": "CN"},
        {"name": "Micron", "ticker": "MU", "country": "US"},
        {"name": "SK Hynix", "ticker": "000660.KS", "country": "KR"},
    ],
    "energy": [
        {"name": "Saudi Aramco", "ticker": "2222.SR", "country": "SA"},
        {"name": "Rosneft", "ticker": "ROSN.ME", "country": "RU"},
        {"name": "Gazprom", "ticker": "GAZP.ME", "country": "RU"},
        {"name": "Sinopec", "ticker": "SNP", "country": "CN"},
        {"name": "PetroChina", "ticker": "PTR", "country": "CN"},
        {"name": "ExxonMobil", "ticker": "XOM", "country": "US"},
        {"name": "Shell", "ticker": "SHEL", "country": "GB"},
    ],
    "shipping": [
        {"name": "COSCO Shipping", "ticker": "1919.HK", "country": "CN"},
        {"name": "Evergreen Marine", "ticker": "2603.TW", "country": "TW"},
        {"name": "Maersk", "ticker": "MAERSK-B.CO", "country": "DK"},
        {"name": "China OOCL", "ticker": "0316.HK", "country": "CN"},
        {"name": "Hapag-Lloyd", "ticker": "HLAG.DE", "country": "DE"},
        {"name": "MSC (private)", "ticker": None, "country": "CH"},
    ],
    "rare earth": [
        {"name": "China Northern Rare Earth", "ticker": "600111.SS", "country": "CN"},
        {"name": "MP Materials", "ticker": "MP", "country": "US"},
        {"name": "Lynas Rare Earths", "ticker": "LYC.AX", "country": "AU"},
        {"name": "Shenghe Resources", "ticker": "600392.SS", "country": "CN"},
    ],
    "telecom": [
        {"name": "Huawei (private)", "ticker": None, "country": "CN"},
        {"name": "ZTE", "ticker": "0763.HK", "country": "CN"},
        {"name": "Ericsson", "ticker": "ERIC", "country": "SE"},
        {"name": "Nokia", "ticker": "NOK", "country": "FI"},
        {"name": "China Mobile", "ticker": "0941.HK", "country": "CN"},
    ],
    "defense_aerospace": [
        {"name": "Lockheed Martin", "ticker": "LMT", "country": "US"},
        {"name": "RTX (Raytheon)", "ticker": "RTX", "country": "US"},
        {"name": "Northrop Grumman", "ticker": "NOC", "country": "US"},
        {"name": "L3Harris Technologies", "ticker": "LHX", "country": "US"},
        {"name": "BAE Systems", "ticker": "BAESY", "country": "GB"},
        {"name": "Leonardo", "ticker": "FINMY", "country": "IT"},
        {"name": "Thales", "ticker": "THLEF", "country": "FR"},
        {"name": "AVIC (private)", "ticker": None, "country": "CN"},
    ],
    "aircraft_mro": [
        {"name": "AAR Corp", "ticker": "AIR", "country": "US"},
        {"name": "Heico Corporation", "ticker": "HEI", "country": "US"},
        {"name": "TransDigm Group", "ticker": "TDG", "country": "US"},
        {"name": "StandardAero (private)", "ticker": None, "country": "US"},
        {"name": "Lufthansa Technik (private)", "ticker": None, "country": "DE"},
        {"name": "ST Engineering", "ticker": "S63.SI", "country": "SG"},
        {"name": "HAECO", "ticker": "0044.HK", "country": "HK"},
        {"name": "VSMPO-AVISMA (titanium supplier)", "ticker": None, "country": "RU"},
    ],
    "critical_minerals": [
        {"name": "MP Materials", "ticker": "MP", "country": "US"},
        {"name": "Lynas Rare Earths", "ticker": "LYC.AX", "country": "AU"},
        {"name": "Albemarle", "ticker": "ALB", "country": "US"},
        {"name": "Ganfeng Lithium", "ticker": "1772.HK", "country": "CN"},
        {"name": "China Northern Rare Earth", "ticker": "600111.SS", "country": "CN"},
        {"name": "Pilbara Minerals", "ticker": "PLS.AX", "country": "AU"},
    ],
    "dual_use_tech": [
        {"name": "DJI (private)", "ticker": None, "country": "CN"},
        {"name": "Hikvision", "ticker": "002415.SZ", "country": "CN"},
        {"name": "Dahua Technology", "ticker": "002236.SZ", "country": "CN"},
        {"name": "SenseTime", "ticker": "0020.HK", "country": "CN"},
        {"name": "Megvii (private)", "ticker": None, "country": "CN"},
    ],
    "port_logistics": [
        {"name": "COSCO Shipping Ports", "ticker": "1199.HK", "country": "CN"},
        {"name": "Hutchison Ports (private)", "ticker": None, "country": "HK"},
        {"name": "DP World (private)", "ticker": None, "country": "AE"},
        {"name": "PSA International (private)", "ticker": None, "country": "SG"},
        {"name": "ICTSI", "ticker": "ICT.PS", "country": "PH"},
    ],
    "financial": [
        {"name": "Sberbank", "ticker": "SBRCY", "country": "RU"},
        {"name": "VTB Bank", "ticker": "VTBR.ME", "country": "RU"},
        {"name": "Bank of China", "ticker": "3988.HK", "country": "CN"},
        {"name": "HSBC", "ticker": "HSBC", "country": "GB"},
        {"name": "Standard Chartered", "ticker": "SCBFF", "country": "GB"},
    ],
    "space_satellite": [
        {"name": "Planet Labs", "ticker": "PL", "country": "US"},
        {"name": "Maxar Technologies (private)", "ticker": None, "country": "US"},
        {"name": "Iridium", "ticker": "IRDM", "country": "US"},
        {"name": "Spire Global", "ticker": "SPIR", "country": "US"},
        {"name": "CASC (private)", "ticker": None, "country": "CN"},
    ],
}

# Aliases for sector matching — maps query terms to registry keys
_SECTOR_ALIASES: dict[str, str] = {
    "mro": "aircraft_mro",
    "aviation maintenance": "aircraft_mro",
    "aircraft repair": "aircraft_mro",
    "aircraft mro": "aircraft_mro",
    "aviation mro": "aircraft_mro",
    "defense": "defense_aerospace",
    "defence": "defense_aerospace",
    "aerospace": "defense_aerospace",
    "defense primes": "defense_aerospace",
    "rare earth": "critical_minerals",
    "rare earths": "critical_minerals",
    "lithium": "critical_minerals",
    "cobalt": "critical_minerals",
    "critical mineral": "critical_minerals",
    "port": "port_logistics",
    "ports": "port_logistics",
    "logistics": "port_logistics",
    "shipping infrastructure": "port_logistics",
    "banking": "financial",
    "finance": "financial",
    "correspondent banking": "financial",
    "surveillance tech": "dual_use_tech",
    "surveillance": "dual_use_tech",
    "dual use": "dual_use_tech",
    "satellite": "space_satellite",
    "space": "space_satellite",
    "commercial space": "space_satellite",
    "chips": "semiconductor",
    "chip": "semiconductor",
    "semis": "semiconductor",
    "oil": "energy",
    "gas": "energy",
    "oil and gas": "energy",
}


def _match_sector(query: str) -> tuple[str, list[dict]]:
    """Find a sector from registry/aliases; no LLM, no generic fallback."""
    q = query.lower().strip()

    # 1. Exact key match
    if q in _SECTOR_COMPANIES:
        return q, _SECTOR_COMPANIES[q]

    # 2. Alias lookup
    if q in _SECTOR_ALIASES:
        key = _SECTOR_ALIASES[q]
        return key, _SECTOR_COMPANIES[key]

    # 3. Substring match against aliases
    for alias, key in _SECTOR_ALIASES.items():
        if alias in q or q in alias:
            return key, _SECTOR_COMPANIES[key]

    # 4. Substring match against registry keys
    for sector_key, companies in _SECTOR_COMPANIES.items():
        if sector_key in q or q in sector_key:
            return sector_key, companies

    # 5. Word-level partial match against registry keys
    for sector_key, companies in _SECTOR_COMPANIES.items():
        words = sector_key.replace("_", " ").split()
        if any(w in q for w in words if len(w) > 3):
            return sector_key, companies

    return "", []


def _clean_ticker_value(raw: Any) -> str | None:
    if raw is None:
        return None
    s = str(raw).strip().upper()
    if not s or s in {"NONE", "NULL", "N/A", "PRIVATE", "-"}:
        return None
    return s


async def _llm_match_sector_key(query: str) -> str:
    """Use the configured LLM to map free text to a known sector key or 'unknown'."""
    client = _get_anthropic_client()
    if not client:
        return "unknown"

    keys = sorted(_SECTOR_COMPANIES.keys())
    prompt = (
        "Classify the sector phrase into ONE known key or 'unknown'. "
        "Return JSON only with schema {\"sector_key\": \"...\"}.\n"
        f"Known keys: {keys}\n"
        f"Input: {query}\n"
        "Rules: if confidence is low, return unknown."
    )
    response = await asyncio.wait_for(
        client.messages.create(
            model=config.model,
            max_tokens=120,
            messages=[{"role": "user", "content": prompt}],
        ),
        timeout=10.0,
    )
    text = response.content[0].text
    payload = json.loads(_extract_json(text))
    key = str(payload.get("sector_key", "unknown")).strip().lower()
    return key if key in _SECTOR_COMPANIES else "unknown"


async def _llm_generate_sector_companies(query: str) -> list[dict[str, Any]]:
    """Generate a lightweight temporary company list for unknown sectors."""
    client = _get_anthropic_client()
    if not client:
        return []

    prompt = (
        "Generate 8 representative companies for the requested sector. "
        "Return JSON only as an array of objects with keys: name, ticker, country.\n"
        "Ticker should be null when private/unknown. Country should be 2-letter code when possible.\n"
        f"Sector query: {query}"
    )
    response = await asyncio.wait_for(
        client.messages.create(
            model=config.model,
            max_tokens=500,
            messages=[{"role": "user", "content": prompt}],
        ),
        timeout=12.0,
    )
    text = response.content[0].text
    rows = json.loads(_extract_json(text))
    if not isinstance(rows, list):
        return []

    companies: list[dict[str, Any]] = []
    for row in rows[:8]:
        if not isinstance(row, dict):
            continue
        name = str(row.get("name", "")).strip()
        if not name:
            continue
        companies.append(
            {
                "name": name,
                "ticker": _clean_ticker_value(row.get("ticker")),
                "country": str(row.get("country", "")).strip().upper()[:2] or None,
            }
        )
    return companies


async def _resolve_sector(query: str) -> tuple[str, list[dict[str, Any]]]:
    """Resolve sector with deterministic matching first, then LLM fallback."""
    key, companies = _match_sector(query)
    if key and companies:
        return key, companies

    try:
        llm_key = await _llm_match_sector_key(query)
    except Exception as exc:
        logger.warning("LLM sector key match failed for query=%r: %s", query, exc)
        llm_key = "unknown"

    if llm_key != "unknown":
        return llm_key, _SECTOR_COMPANIES[llm_key]

    try:
        dynamic_companies = await _llm_generate_sector_companies(query)
    except Exception as exc:
        logger.warning("LLM dynamic sector company generation failed for query=%r: %s", query, exc)
        dynamic_companies = []

    dynamic_key = query.lower().strip().replace(" ", "_")[:40] or "unknown"
    return dynamic_key, dynamic_companies


@app.post("/api/sector-analysis")
async def sector_analysis(req: SectorAnalysisRequest):
    """Sector-level analysis: key players, sanctions exposure, trade dependency."""
    sector = req.sector.strip()
    if not sector:
        raise HTTPException(status_code=400, detail="Sector cannot be empty")

    try:
        sector_key, companies = await _resolve_sector(sector)
        if not companies:
            raise HTTPException(
                status_code=422,
                detail=(
                    "Could not resolve sector to a supported registry key and failed "
                    "to generate a dynamic company set. Try a more specific sector phrase."
                ),
            )

        # Check OFAC status for top companies in parallel
        ofac_client = OFACClient()
        sanction_tasks = [ofac_client.search(co["name"]) for co in companies]
        sanction_results = await asyncio.gather(*sanction_tasks, return_exceptions=True)

        company_profiles = []
        for co, result in zip(companies, sanction_results):
            hits = result if not isinstance(result, Exception) else []
            high_conf = [
                e
                for e in hits
                if (e.score or 0) >= 0.75 and _ofac_hit_matches_company_label(co["name"], e)
            ] if hits else []
            company_profiles.append({
                "name": co["name"],
                "ticker": co.get("ticker"),
                "country": co.get("country"),
                "is_sanctioned": bool(high_conf),
                "sanction_names": [e.name for e in high_conf[:2]],
            })

        sanctioned_count = sum(1 for c in company_profiles if c["is_sanctioned"])

        # Optional enrichment for defense/aviation-style sectors.
        supply_chain_exposures: list[dict[str, Any]] = []
        geopolitical_tensions: list[dict[str, Any]] = []

        sector_hint = f"{sector_key} {sector}".lower()
        if any(k in sector_hint for k in ("aircraft", "mro", "defense", "aerospace")):
          commodity_specs = [
            ("titanium", "810890"),
            ("carbon_fiber", "681510"),
            ("rare_earth_magnets", "850511"),
          ]
          supply_tasks = [
            get_supply_chain_exposure(country="USA", commodity_code=code)
            for _name, code in commodity_specs
          ]
          tension_tasks = [
            get_bilateral_tensions("United States", "China", days=180),
            get_bilateral_tensions("United States", "Russia", days=180),
          ]
          supply_results, tension_results = await asyncio.gather(
            asyncio.gather(*supply_tasks, return_exceptions=True),
            asyncio.gather(*tension_tasks, return_exceptions=True),
          )

          for (label, code), result in zip(commodity_specs, supply_results):
            if isinstance(result, Exception):
              continue
            payload = result.get("data", result)
            supply_chain_exposures.append(
              {
                "label": label,
                "commodity_code": code,
                "import_share_pct": payload.get("import_share_pct", 0.0),
                "top_suppliers": payload.get("top_suppliers", [])[:5],
              }
            )

          for pair, result in zip(("US-China", "US-Russia"), tension_results):
            if isinstance(result, Exception):
              continue
            payload = result.get("data", result)
            geopolitical_tensions.append(
              {
                "pair": pair,
                "event_count": payload.get("event_count", 0),
                "tension_level": payload.get("tension_level", "unknown"),
                "avg_tone": payload.get("avg_tone"),
              }
            )

        # Build sector vis.js graph
        nodes: dict[str, dict] = {}
        edges: dict[str, dict] = {}

        def s_slug(s: str) -> str:
            return s.lower().replace(" ", "_").replace("(", "").replace(")", "")[:60]

        sector_id = f"sector_{s_slug(sector_key)}"
        nodes[sector_id] = _node(sector_id, sector_key.title() + " Sector", "sector")

        for co in company_profiles:
            cid = f"co_{s_slug(co['name'])}"
            etype = "sanctions_list" if co["is_sanctioned"] else "company"
            nodes[cid] = _node(cid, co["name"], etype, co.get("country"))
            edges[f"{sector_id}→{cid}"] = {
                "from": sector_id, "to": cid,
                "label": "key player", "arrows": "to", "dashes": False,
            }
            if co["is_sanctioned"]:
                for sn in co["sanction_names"][:1]:
                    sid = f"sanc_{s_slug(sn)}"
                    nodes[sid] = _node(sid, sn, "sanctions_list")
                    edges[f"{cid}→{sid}"] = {
                        "from": cid, "to": sid, "label": "OFAC listed",
                        "arrows": "to", "dashes": True,
                    }

        # Start narrative generation concurrently with graph finalization
        compact = {
            "sector": sector_key,
            "company_count": len(company_profiles),
            "sanctioned_count": sanctioned_count,
            "sanctioned_entities": [
                {"name": c["name"], "country": c["country"]}
                for c in company_profiles if c["is_sanctioned"]
            ],
            "key_players": [
                {"name": c["name"], "country": c["country"], "ticker": c["ticker"]}
                for c in company_profiles[:6]
            ],
              "supply_chain_exposure_count": len(supply_chain_exposures),
              "geopolitical_tension_pairs": geopolitical_tensions,
        }
        sector_prompt = (
            f"You are an economic warfare analyst. Given the following data on the "
            f"{sector_key.replace('_', ' ')} sector, write 3-5 sentences identifying the "
            f"most significant risk vectors: entity sanctions exposure, supply chain "
            f"concentration, geopolitical exposure, and regulatory trajectory.\n"
            f"Data: {json.dumps(compact)}"
        )
        narrative_task = asyncio.create_task(_generate_narrative(sector_prompt))

        graph_result = {
            "nodes": list(nodes.values()),
            "edges": list(edges.values()),
        }
        narrative = await narrative_task

        return JSONResponse(content={
            "sector": sector,
            "sector_key": sector_key,
            "company_count": len(company_profiles),
            "sanctioned_count": sanctioned_count,
            "companies": company_profiles,
            "graph": graph_result,
            "narrative": narrative,
            "supply_chain_exposures": supply_chain_exposures,
            "geopolitical_tensions": geopolitical_tensions,
            "sources": ["OFAC SDN", "OpenSanctions"],
        })

    except Exception as e:
        logger.exception("sector_analysis error for sector=%s", sector)
        raise HTTPException(status_code=500, detail=str(e))


# --- Entity Risk Report endpoint ---

class EntityRiskReportRequest(BaseModel):
    name: str
    entity_type: str = "company"  # company, person, vessel, sanctions_list, sector, sayari
    ticker: str | None = None
    lei: str | None = None


_HIGH_RISK_COUNTRIES = {
    "RU", "IR", "KP", "BY", "CU", "SY", "VE", "MM", "SD", "SS",
    "CF", "CD", "IQ", "LB", "LY", "SO", "YE", "ZW",
}
_ELEVATED_RISK_COUNTRIES = {"CN", "HK", "TR", "AE", "SA", "PK", "NG", "UA", "UZ", "KZ"}

# Maps full country names (as returned by yfinance) to ISO-2 codes.
# GLEIF already returns ISO-2 codes. Unknown names resolve to None → risk=LOW (safe).
_COUNTRY_NAME_TO_ISO: dict[str, str] = {
    # Risk-relevant
    "china": "CN", "hong kong": "HK", "russia": "RU", "iran": "IR",
    "north korea": "KP", "belarus": "BY", "cuba": "CU", "syria": "SY",
    "venezuela": "VE", "myanmar": "MM", "burma": "MM", "sudan": "SD",
    "turkey": "TR", "united arab emirates": "AE", "saudi arabia": "SA",
    "pakistan": "PK", "nigeria": "NG", "ukraine": "UA", "uzbekistan": "UZ",
    "kazakhstan": "KZ", "south sudan": "SS", "central african republic": "CF",
    "dr congo": "CD", "democratic republic of the congo": "CD", "iraq": "IQ",
    "lebanon": "LB", "libya": "LY", "somalia": "SO", "yemen": "YE",
    "zimbabwe": "ZW",
    # Common yfinance country names (not risk-scored but needed for accurate display)
    "taiwan": "TW", "united states": "US", "germany": "DE", "japan": "JP",
    "france": "FR", "united kingdom": "GB", "netherlands": "NL",
    "south korea": "KR", "india": "IN", "singapore": "SG", "canada": "CA",
    "australia": "AU", "brazil": "BR", "mexico": "MX", "israel": "IL",
    "sweden": "SE", "switzerland": "CH", "spain": "ES", "italy": "IT",
}


def _country_to_iso(country: str | None) -> str | None:
    """Normalise a country string to an ISO-2 code for risk scoring.
    Returns None for unknown country names — risk scoring treats that as LOW."""
    if not country:
        return None
    c = country.strip()
    if len(c) == 2:
        return c.upper()
    return _COUNTRY_NAME_TO_ISO.get(c.lower())  # None if unknown — don't guess


@app.post("/api/entity-risk-report")
async def entity_risk_report(req: EntityRiskReportRequest):
    """Generate a focused risk report for an entity node from the entity graph.

    Runs all data sources in parallel:
      OFAC SDN · Trade.gov CSL · GLEIF (corporate structure + parent chain) ·
      OpenCorporates (officers) · ICIJ Offshore Leaks ·
      Yahoo Finance (profile, price, 52w range, institutional holders, analyst consensus)
    """
    name = req.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="Entity name cannot be empty")

    entity_type = req.entity_type.lower()
    ticker = (req.ticker or "").strip().upper() or None

    try:
        from datetime import datetime as _dt

        yf_client = YFinanceClient()
        ofac_client = OFACClient()
        sanctions_client = SanctionsClient()

        # ── Fire all data sources in parallel ────────────────────────────
        async def _safe(coro, default):
            try:
                return await asyncio.wait_for(coro, timeout=12.0)
            except Exception:
                return default

        tasks: dict[str, Any] = {
            "ofac": _safe(ofac_client.search(name), []),
            "csl": _safe(search_csl(name), []),
            "gleif": _safe(gleif_search_lei(name), []),
            "oc": _safe(oc_search_companies(name), []),
            # ICIJ /api/v1/search is currently 404 — skip to avoid wasted latency
        }
        if ticker:
            tasks["yf_profile"] = _safe(yf_client.get_stock_profile(ticker), None)
            tasks["yf_price"] = _safe(yf_client.get_price_data(ticker, period="1y"), None)
            tasks["yf_holders"] = _safe(yf_client.get_institutional_holders(ticker), [])
            tasks["yf_analyst"] = _safe(yf_client.get_analyst_estimate(ticker), None)

        keys = list(tasks.keys())
        results = await asyncio.gather(*[tasks[k] for k in keys])
        r = dict(zip(keys, results))

        # ── Sanctions ────────────────────────────────────────────────────
        ofac_hits = [e for e in (r["ofac"] or []) if (e.score or 0) >= 0.75]
        csl_raw = sanctions_client._csl_to_entries(r["csl"] or [])
        csl_hits = [e for e in csl_raw if (e.score or 0) >= 0.6]

        is_sanctioned = bool(ofac_hits or csl_hits)
        sanction_programs: list[str] = []
        sanction_lists: list[str] = []
        sanction_details: list[dict[str, Any]] = []

        for e in ofac_hits:
            if e.programs:
                sanction_programs.extend(e.programs)
            sanction_lists.append("OFAC SDN")
            sanction_details.append({
                "name": e.name,
                "score": round(e.score or 0, 2),
                "programs": (e.programs or [])[:3],
                "remarks": (e.remarks or "")[:200] or None,
            })
        for e in csl_hits:
            if e.programs:
                sanction_programs.extend(e.programs)
            sanction_lists.append("Trade.gov CSL")

        sanction_programs = list(dict.fromkeys(sanction_programs))[:6]
        sanction_lists = list(dict.fromkeys(sanction_lists))

        # ── Corporate structure (GLEIF) ───────────────────────────────────
        corporate_info: dict[str, Any] = {}
        country: str | None = None

        def _name_matches(query: str, candidate: str) -> bool:
            """Check that a GLEIF/OC result name actually corresponds to the query.

            For queries with 4+ char tokens, require at least one shared token.
            For short names (e.g. ZTE, BYD), fall back to case-insensitive substring.
            """
            q_low, c_low = query.lower(), candidate.lower()
            q_tokens = set(re.findall(r"[a-z0-9]{4,}", q_low))
            if not q_tokens:
                # Short ticker/acronym: require the full query as a word boundary match
                return bool(re.search(r"\b" + re.escape(q_low) + r"\b", c_low))
            c_tokens = set(re.findall(r"[a-z0-9]{4,}", c_low))
            return bool(q_tokens & c_tokens)

        lei_records = r.get("gleif") or []
        lei_records = [rec for rec in lei_records if _name_matches(name, rec.legal_name)]

        # Pull GLEIF structural data (LEI, status) but don't commit country yet —
        # yfinance is more authoritative for listed companies' actual HQ.
        gleif_country: str | None = None
        if lei_records:
            rec = lei_records[0]
            gleif_country = rec.country
            corporate_info = {
                "legal_name": rec.legal_name,
                "lei": rec.lei,
                "country": rec.country,
                "status": rec.status,
            }
            try:
                parent = await asyncio.wait_for(gleif_get_ultimate_parent(rec.lei), timeout=6.0)
                if parent:
                    corporate_info["ultimate_parent_lei"] = parent.parent_id
            except Exception:
                pass

        # Officers from OpenCorporates
        oc_companies = [c for c in (r.get("oc") or []) if _name_matches(name, c.name)]
        officers: list[dict[str, str]] = []
        oc_country: str | None = None
        if oc_companies:
            oc_co = oc_companies[0]
            if oc_co.jurisdiction:
                oc_country = oc_co.jurisdiction.split("_")[0].upper()
            if not corporate_info.get("legal_name"):
                corporate_info["legal_name"] = oc_co.name
            if oc_co.incorporation_date:
                corporate_info["incorporation_date"] = str(oc_co.incorporation_date)
            if oc_co.registered_address:
                corporate_info["registered_address"] = oc_co.registered_address
            if oc_co.status:
                corporate_info.setdefault("status", oc_co.status)
            for off in (oc_co.officers or [])[:5]:
                officers.append({"name": off.name, "role": off.role or ""})

        # If officers not from OC, try the officer-by-name endpoint
        if not officers:
            try:
                off_list = await asyncio.wait_for(oc_search_officers(name), timeout=8.0)
                for off in off_list[:5]:
                    officers.append({"name": off.name, "role": off.role or ""})
            except Exception:
                pass

        # ICIJ offshore connections (API currently unavailable — always empty)
        offshore_flags: list[dict[str, str]] = []

        # ── Market data (ticker) ──────────────────────────────────────────
        market_info: dict[str, Any] | None = None
        exposure: dict[str, Any] | None = None
        yf_country: str | None = None

        if ticker:
            profile = r.get("yf_profile")
            price_data = r.get("yf_price")
            holders = r.get("yf_holders") or []
            analyst = r.get("yf_analyst")

            if profile:
                yf_country = profile.country  # most authoritative for listed companies
            if profile and not corporate_info.get("legal_name"):
                corporate_info["legal_name"] = profile.name

            if price_data or profile:
                current_price = price_data.current_price if price_data else None
                change_pct = price_data.change_pct if price_data else None
                hi52 = price_data.fifty_two_week_high if price_data else None
                lo52 = price_data.fifty_two_week_low if price_data else None
                pct_from_hi = (
                    round((current_price - hi52) / hi52 * 100, 1)
                    if current_price and hi52 else None
                )
                market_info = {
                    "ticker": ticker,
                    "current_price": round(current_price, 2) if current_price else None,
                    "market_cap": profile.market_cap if profile else None,
                    "change_pct": round(change_pct, 2) if change_pct else None,
                    "sector": profile.sector if profile else None,
                    "industry": profile.industry if profile else None,
                    "exchange": profile.exchange if profile else None,
                    "fifty_two_week_high": round(hi52, 2) if hi52 else None,
                    "fifty_two_week_low": round(lo52, 2) if lo52 else None,
                    "pct_from_52w_high": pct_from_hi,
                    "analyst_target": analyst.target_price if analyst else None,
                    "analyst_recommendation": analyst.recommendation if analyst else None,
                    "analyst_count": analyst.num_analysts if analyst else None,
                    "description": (profile.description or "")[:300] if profile and profile.description else None,
                }

            # Institutional holder exposure
            if holders:
                top_holders = []
                for h in sorted(holders, key=lambda x: x.pct_held or 0, reverse=True)[:8]:
                    top_holders.append({
                        "name": h.holder_name,
                        "pct_held": round(h.pct_held * 100, 2) if h.pct_held and h.pct_held < 1 else h.pct_held,
                        "value_usd": h.value,
                        "is_pension": _is_pension_or_sovereign(h.holder_name),
                    })
                pension_holders = [h for h in top_holders if h["is_pension"]]
                total_usd = sum(h["value_usd"] for h in top_holders if h["value_usd"])
                exposure = {
                    "top_holders": top_holders,
                    "pension_count": len(pension_holders),
                    "pension_names": [h["name"] for h in pension_holders][:3],
                    "total_institutional_usd": total_usd if total_usd else None,
                }

        # ── Resolve authoritative country ─────────────────────────────────
        # Priority: yfinance (actual HQ) > OC > GLEIF (may be a subsidiary's country)
        country = yf_country or oc_country or gleif_country
        country_iso = _country_to_iso(country)

        # ── Risk indicators ───────────────────────────────────────────────
        risk_indicators: list[dict[str, str]] = []

        risk_indicators.append({
            "label": "Sanctions",
            "value": "DESIGNATED" if is_sanctioned else "Clear",
            "severity": "high" if is_sanctioned else "low",
        })

        if sanction_programs:
            risk_indicators.append({
                "label": "OFAC Programs",
                "value": ", ".join(sanction_programs[:3]),
                "severity": "high",
            })

        if country:
            sev = "high" if country_iso in _HIGH_RISK_COUNTRIES else (
                "medium" if country_iso in _ELEVATED_RISK_COUNTRIES else "low"
            )
            risk_indicators.append({"label": "Jurisdiction", "value": country, "severity": sev})

        if corporate_info.get("status"):
            st = corporate_info["status"]
            risk_indicators.append({
                "label": "Entity Status",
                "value": st,
                "severity": "low" if st in ("ACTIVE", "ISSUED") else "medium",
            })

        if market_info:
            if market_info.get("market_cap"):
                mc = market_info["market_cap"]
                mc_str = f"${mc / 1e12:.2f}T" if mc >= 1e12 else (f"${mc / 1e9:.1f}B" if mc >= 1e9 else f"${mc / 1e6:.0f}M")
                risk_indicators.append({"label": "Market Cap", "value": mc_str, "severity": "low"})
            if market_info.get("pct_from_52w_high") is not None:
                pct = market_info["pct_from_52w_high"]
                sev = "high" if pct < -30 else ("medium" if pct < -15 else "low")
                risk_indicators.append({
                    "label": "vs 52-Week High",
                    "value": f"{pct:+.1f}%",
                    "severity": sev,
                })
            if market_info.get("analyst_recommendation"):
                risk_indicators.append({
                    "label": "Analyst Consensus",
                    "value": market_info["analyst_recommendation"].upper(),
                    "severity": "low",
                })

        if exposure and exposure.get("pension_count", 0) > 0:
            risk_indicators.append({
                "label": "Friendly Fire",
                "value": f"{exposure['pension_count']} US pension/sovereign fund(s) exposed",
                "severity": "medium",
            })

        # ── Overall risk level ────────────────────────────────────────────
        if is_sanctioned or country_iso in _HIGH_RISK_COUNTRIES or offshore_flags:
            risk_level = "HIGH"
        elif ofac_hits or country_iso in _ELEVATED_RISK_COUNTRIES:
            risk_level = "MEDIUM"
        else:
            risk_level = "LOW"

        # ── Narrative — feed Claude real numbers ─────────────────────────
        narrative_data: dict[str, Any] = {
            "name": name,
            "entity_type": entity_type,
            "is_sanctioned": is_sanctioned,
            "sanction_programs": sanction_programs,
            "sanction_details": sanction_details[:2],
            "country": country,
            "risk_level": risk_level,
            "officers": officers[:3],
            "offshore_connections": len(offshore_flags),
            "offshore_jurisdictions": [f["jurisdiction"] for f in offshore_flags[:3]],
        }
        if market_info:
            narrative_data["market_cap"] = market_info.get("market_cap")
            narrative_data["sector"] = market_info.get("sector")
            narrative_data["industry"] = market_info.get("industry")
            narrative_data["current_price"] = market_info.get("current_price")
            narrative_data["pct_from_52w_high"] = market_info.get("pct_from_52w_high")
            narrative_data["analyst_target"] = market_info.get("analyst_target")
            narrative_data["analyst_recommendation"] = market_info.get("analyst_recommendation")
        if exposure:
            narrative_data["pension_funds_exposed"] = exposure.get("pension_names")
            narrative_data["total_institutional_usd"] = exposure.get("total_institutional_usd")

        narrative_prompt = (
            f"You are an economic warfare intelligence analyst. Write a 4-6 sentence risk "
            f"assessment for '{name}' (type: {entity_type}) as of {date.today().isoformat()}. "
            f"Be specific: cite real numbers from the data (market cap, price vs 52-week high, "
            f"analyst target, pension fund names, sanction programs, offshore jurisdictions). "
            f"Cover: (1) sanctions/designation status with any specific program names, "
            f"(2) if publicly traded — where the stock sits relative to 52-week range and what "
            f"analyst consensus implies about trajectory, "
            f"(3) any offshore/corporate structure risks, "
            f"(4) friendly-fire exposure to US/allied institutional investors if applicable. "
            f"If data is sparse, state what the absence of derogatory findings means. "
            f"Do not invent numbers. Use only what is in the data below.\n"
            f"Data: {json.dumps(narrative_data)}"
        )
        narrative = await _generate_narrative(narrative_prompt)

        sources = ["OFAC SDN", "Trade.gov CSL"]
        if lei_records:
            sources.append("GLEIF")
        if oc_companies or officers:
            sources.append("OpenCorporates")
        if ticker and market_info:
            sources.append("Yahoo Finance")

        return JSONResponse(content={
            "name": name,
            "entity_type": entity_type,
            "risk_level": risk_level,
            "is_sanctioned": is_sanctioned,
            "sanction_programs": sanction_programs,
            "sanction_lists": sanction_lists,
            "sanction_details": sanction_details[:3],
            "country": country,
            "corporate_info": corporate_info,
            "officers": officers,
            "offshore_flags": offshore_flags,
            "market_info": market_info,
            "exposure": exposure,
            "risk_indicators": risk_indicators,
            "narrative": narrative,
            "sources": sources,
            "generated_at": _dt.utcnow().isoformat() + "Z",
        })

    except Exception as e:
        logger.exception("entity_risk_report error for name=%s", name)
        raise HTTPException(status_code=500, detail=str(e))


# --- Follow-up Q&A ---

class FollowUpMessage(BaseModel):
    role: str   # 'user' | 'assistant'
    text: str


class FollowUpRequest(BaseModel):
    question: str
    context_type: str  # 'company' | 'orchestrator'
    context: dict[str, Any]
    history: list[FollowUpMessage] = []


class FollowUpResponse(BaseModel):
    answer: str


def _fmt_pct(v: Any) -> str:
    if v is None:
        return "N/A"
    return f"{float(v):+.1f}%"


def _fmt_mc(v: Any) -> str:
    if not v:
        return "N/A"
    v = float(v)
    if v >= 1e12:
        return f"${v / 1e12:.2f}T"
    if v >= 1e9:
        return f"${v / 1e9:.1f}B"
    if v >= 1e6:
        return f"${v / 1e6:.0f}M"
    return f"${v:,.0f}"


def _build_company_followup_system(ctx: dict[str, Any]) -> str:
    target = ctx.get("target", {})
    proj = ctx.get("projection", {})
    summary = proj.get("summary", {})
    comparables = ctx.get("comparables", [])
    control_comparables = ctx.get("control_comparables", [])
    narrative = ctx.get("narrative", "")
    sanctions = target.get("sanctions_status", {})
    metadata = ctx.get("metadata", {})

    # Sanctions detail
    csl_matches = sanctions.get("csl_matches", [])
    csl_lines = "\n".join(
        f"    · {m.get('name')} | Source: {m.get('source')} | Programs: {', '.join(m.get('programs', [])) or 'N/A'}"
        + (f" | Start: {m.get('start_date')}" if m.get('start_date') else "")
        for m in csl_matches[:5]
    )

    # Comparables — full detail
    comp_lines = "\n".join(
        f"  [{i+1}] {c.get('name')} ({c.get('ticker')}) — sanctioned {str(c.get('sanction_date', ''))[:10]}"
        f"\n      Type: {c.get('sanction_type') or 'N/A'} | Sector: {c.get('sector') or 'N/A'}"
        f"\n      Context: {c.get('description') or 'N/A'}"
        for i, c in enumerate(comparables)
    )

    ctrl_lines = "\n".join(
        f"  - {c.get('name')} ({c.get('ticker')})"
        for c in control_comparables
    )

    day30_range = summary.get("day_30_range")
    day60_range = summary.get("day_60_range")
    day90_range = summary.get("day_90_range")
    r30 = f"({_fmt_pct(day30_range[0])} to {_fmt_pct(day30_range[1])})" if day30_range else ""
    r60 = f"({_fmt_pct(day60_range[0])} to {_fmt_pct(day60_range[1])})" if day60_range else ""
    r90 = f"({_fmt_pct(day90_range[0])} to {_fmt_pct(day90_range[1])})" if day90_range else ""

    coherence = proj.get("coherence_score")
    coherence_str = f"{coherence * 100:.0f}% directional agreement" if coherence is not None else "N/A"
    sourcing = metadata.get("sourcing_method", "unknown")

    return f"""You are a senior economic warfare intelligence analyst. Answer every question with a direct, confident judgment. You have the full data below — use it to give a definitive answer, not a hedge.

═══ TARGET ═══
Company:      {target.get('name')} ({target.get('ticker')})
Sector:       {target.get('sector')} | Industry: {target.get('industry')} | Country: {target.get('country')}
Price:        ${float(target.get('current_price') or 0):.2f} (day change: {_fmt_pct(target.get('change_pct'))})
Market Cap:   {_fmt_mc(target.get('market_cap'))}
Sanctioned:   {sanctions.get('is_sanctioned')}
Programs:     {', '.join(sanctions.get('programs', [])) or 'None'}
Lists:        {', '.join(sanctions.get('lists', [])) or 'None'}
{f"CSL Matches:{chr(10)}{csl_lines}" if csl_lines else "CSL Matches:  None"}

═══ PROJECTION (excess return vs sector ETF, based on {len(comparables)} comparable sanctions events) ═══
  60d pre-event:  {_fmt_pct(summary.get('pre_event_decline'))}
  30d post-event: {_fmt_pct(summary.get('day_30_post'))} {r30}
  60d post-event: {_fmt_pct(summary.get('day_60_post'))} {r60}
  90d post-event: {_fmt_pct(summary.get('day_90_post'))} {r90}
  Max drawdown:   {_fmt_pct(summary.get('max_drawdown'))}
  Coherence:      {coherence_str}
  Sourcing:       {sourcing}

═══ SANCTIONED COMPARABLE CASES ({len(comparables)}) ═══
{comp_lines or '  None — no comparable cases were found.'}

═══ CONTROL GROUP — NON-SANCTIONED PEERS ({len(control_comparables)}) ═══
{ctrl_lines or '  None'}

═══ ANALYST NARRATIVE ═══
{narrative or 'None generated.'}

─── HOW TO ANSWER ───
• Lead with the answer. State your conclusion in the first sentence, then back it with numbers.
• Cite exact figures, company names, dates, and programs — don't describe the data, use it.
• When asked about trajectory or risk, state what the comparables show and commit to the most likely outcome.
• Never hedge with phrases like "it's unclear," "we can't be certain," or "more data would be needed." Make the call from what you have.
• Do NOT pad. Answer directly, then stop."""


def _build_orchestrator_followup_system(ctx: dict[str, Any]) -> str:
    query_info = ctx.get("query", {})
    scenario = ctx.get("scenario_type", "").replace("_", " ")
    exec_summary = ctx.get("executive_summary", "")
    findings = ctx.get("findings", [])
    friendly_fire = ctx.get("friendly_fire", [])
    recommendations = ctx.get("recommendations", [])
    confidence_summary = ctx.get("confidence_summary", {})
    tool_results = ctx.get("tool_results", {})

    findings_text = "\n".join(
        f"  [{f.get('confidence', '?')}] {f.get('category', '')}: {f.get('finding', '')}"
        for f in findings
    )
    ff_text = "\n".join(
        f"  - {ff.get('entity')}: {ff.get('details') or [ff.get('exposure_type'), ff.get('estimated_impact')] and ' | '.join(filter(None, [ff.get('exposure_type'), ff.get('estimated_impact')])) or '—'}"
        for ff in friendly_fire
    )
    rec_text = "\n".join(f"  {i+1}. {r}" for i, r in enumerate(recommendations))
    conf_text = "\n".join(f"  {k}: {v}" for k, v in confidence_summary.items())

    # Serialize raw tool results, truncated to stay within context limits
    tool_results_json = json.dumps(tool_results, indent=2, default=str)
    if len(tool_results_json) > 40000:
        tool_results_json = tool_results_json[:40000] + "\n... [truncated]"

    return f"""You are a senior economic warfare intelligence analyst. Answer every question with a direct, confident judgment. You have the full pipeline data below — use it to give a definitive answer, not a hedge.

═══ ORIGINAL QUERY ═══
{query_info.get('raw_query', 'N/A')}
Scenario type: {scenario}

═══ EXECUTIVE SUMMARY ═══
{exec_summary}

═══ FINDINGS ({len(findings)} total) ═══
{findings_text or '  None'}

═══ FRIENDLY FIRE ALERTS ({len(friendly_fire)}) ═══
{ff_text or '  None'}

═══ RECOMMENDATIONS ═══
{rec_text or '  None'}

═══ CONFIDENCE BY DOMAIN ═══
{conf_text or '  None'}

═══ RAW PIPELINE DATA (all tool results) ═══
{tool_results_json or '  None collected'}

─── HOW TO ANSWER ───
• Lead with the answer. State your conclusion in the first sentence, then back it with specifics from the data.
• Cite exact entities, figures, finding categories, and dates — don't describe the data, use it to make an argument.
• Commit to the most supported interpretation. Do not present two equal alternatives when the data favors one.
• Never hedge with phrases like "it's unclear," "we can't be certain," "the data is limited," or "more research is needed." Make the call from what you have.
• Do NOT pad. Answer directly, then stop."""


@app.post("/api/followup", response_model=FollowUpResponse)
async def followup(req: FollowUpRequest) -> FollowUpResponse:
    """Answer a follow-up question grounded in the current analysis context."""
    client = _get_anthropic_client()
    if not client:
        raise HTTPException(status_code=503, detail="AI not configured")

    question = req.question.strip()
    if not question:
        raise HTTPException(status_code=400, detail="Question cannot be empty")

    if req.context_type == "company":
        system_prompt = _build_company_followup_system(req.context)
    else:
        system_prompt = _build_orchestrator_followup_system(req.context)

    # Build multi-turn message list from history + current question
    messages: list[dict[str, str]] = []
    for msg in req.history:
        messages.append({"role": msg.role, "content": msg.text})
    messages.append({"role": "user", "content": question})

    try:
        response = await asyncio.wait_for(
            client.messages.create(
                model=config.model,
                max_tokens=2500,
                system=system_prompt,
                messages=messages,
            ),
            timeout=60.0,
        )
        return FollowUpResponse(answer=response.content[0].text.strip())
    except Exception as exc:
        logger.warning("followup failed: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


# --- Legacy note ---
# A previous revision had the async analysis runner disabled in this section.
# The active implementation now lives near the /api/analyze endpoints above.



# --- Inline frontend ---

def _read_index_html() -> str:
    """Return the embedded single-page frontend."""
    return """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Economic Warfare OSINT — Sanctions Impact Projector</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
<script src="https://cdn.jsdelivr.net/npm/chartjs-chart-sankey@0.12"></script>
<script src="https://cdn.jsdelivr.net/npm/chartjs-plugin-annotation"></script>
<script src="https://cdn.jsdelivr.net/npm/vis-network@9.1.9/standalone/umd/vis-network.min.js"></script>
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/vis-network@9.1.9/styles/vis-network.min.css">
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css">
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<style>
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body { font-family: 'Segoe UI', system-ui, -apple-system, sans-serif; background: #0a0e17; color: #c9d1d9; min-height: 100vh; }

  .header { background: linear-gradient(135deg, #0d1117 0%, #161b22 100%); border-bottom: 1px solid #30363d; padding: 20px 32px; }
  .header h1 { font-size: 24px; color: #e6edf3; font-weight: 600; }
  .header p { color: #8b949e; font-size: 14px; margin-top: 4px; }

  .main { max-width: 1400px; margin: 0 auto; padding: 24px 32px; }

  .query-box { background: #161b22; border: 1px solid #30363d; border-radius: 8px; padding: 20px; margin-bottom: 24px; }
  .query-box input[type="text"] { width: 100%; background: #0d1117; border: 1px solid #30363d; border-radius: 6px; color: #e6edf3; padding: 14px 16px; font-size: 16px; font-family: inherit; }
  .query-box input[type="text"]:focus { outline: none; border-color: #58a6ff; box-shadow: 0 0 0 3px rgba(88,166,255,0.15); }
  .query-box input[type="text"]::placeholder { color: #484f58; }

  .btn-row { display: flex; gap: 12px; margin-top: 12px; align-items: center; }
  .btn { padding: 8px 20px; border-radius: 6px; border: 1px solid #30363d; cursor: pointer; font-size: 14px; font-weight: 500; transition: all 0.15s; }
  .btn-primary { background: #238636; border-color: #238636; color: #fff; }
  .btn-primary:hover { background: #2ea043; }
  .btn-primary:disabled { opacity: 0.5; cursor: not-allowed; }
  .btn-secondary { background: #21262d; color: #c9d1d9; }
  .btn-secondary:hover { background: #30363d; }

  .examples { display: flex; gap: 8px; flex-wrap: wrap; margin-top: 12px; }
  .example-chip { background: #1c2129; border: 1px solid #30363d; border-radius: 16px; padding: 5px 14px; font-size: 12px; color: #8b949e; cursor: pointer; transition: all 0.15s; }
  .example-chip:hover { border-color: #58a6ff; color: #58a6ff; }

  .progress-panel { background: #161b22; border: 1px solid #30363d; border-radius: 8px; padding: 16px; margin-bottom: 24px; display: none; }
  .progress-panel.active { display: block; }
  .progress-panel h3 { font-size: 14px; color: #8b949e; margin-bottom: 8px; text-transform: uppercase; letter-spacing: 0.5px; }
  .progress-log { font-family: 'Cascadia Code', 'Fira Code', monospace; font-size: 13px; line-height: 1.6; }
  .progress-log .step { color: #58a6ff; }
  .progress-log .error { color: #f85149; }
  .progress-log .done { color: #3fb950; }
  .spinner { display: inline-block; width: 12px; height: 12px; border: 2px solid #30363d; border-top-color: #58a6ff; border-radius: 50%; animation: spin 0.8s linear infinite; margin-right: 8px; }
  @keyframes spin { to { transform: rotate(360deg); } }

  .status-badge { display: inline-block; padding: 2px 8px; border-radius: 12px; font-size: 12px; font-weight: 500; }
  .status-badge.ok { background: #238636; color: #fff; }
  .status-badge.error { background: #da3633; color: #fff; }

  /* Impact Projector styles */
  .impact-chart-container { background: #0d1117; border: 1px solid #30363d; border-radius: 8px; padding: 20px; margin-bottom: 24px; }
  .impact-chart-container canvas { width: 100% !important; height: 450px !important; }

  .impact-info { display: grid; grid-template-columns: 1fr 1fr; gap: 20px; margin-bottom: 24px; }
  @media (max-width: 900px) { .impact-info { grid-template-columns: 1fr; } }

  .info-card { background: #161b22; border: 1px solid #30363d; border-radius: 8px; padding: 20px; }
  .info-card h3 { font-size: 14px; color: #8b949e; text-transform: uppercase; letter-spacing: 0.5px; margin-bottom: 12px; }
  .info-card .value { font-size: 28px; font-weight: 600; color: #e6edf3; }
  .info-card .label { font-size: 12px; color: #8b949e; margin-top: 4px; }
  .info-card .sub-value { font-size: 14px; color: #c9d1d9; margin-top: 8px; }

  .sanctions-badge { display: inline-block; padding: 3px 10px; border-radius: 12px; font-size: 11px; font-weight: 600; text-transform: uppercase; letter-spacing: 0.5px; }
  .sanctions-badge.sanctioned { background: rgba(248, 81, 73, 0.15); color: #f85149; border: 1px solid rgba(248, 81, 73, 0.3); }
  .sanctions-badge.clear { background: rgba(63, 185, 80, 0.15); color: #3fb950; border: 1px solid rgba(63, 185, 80, 0.3); }

  .comparables-table { width: 100%; border-collapse: collapse; margin-top: 12px; }
  .comparables-table th { background: #21262d; color: #8b949e; font-size: 11px; text-transform: uppercase; letter-spacing: 0.5px; padding: 8px 12px; text-align: left; }
  .comparables-table td { padding: 8px 12px; border-bottom: 1px solid #21262d; font-size: 13px; }
  .comparables-table .color-dot { display: inline-block; width: 10px; height: 10px; border-radius: 50%; margin-right: 8px; vertical-align: middle; }
  .comparables-table tr { cursor: pointer; transition: opacity 0.15s; }
  .comparables-table tr:hover { background: #21262d; }
  .comparables-table tr.dimmed { opacity: 0.35; }

  .projection-summary { display: grid; grid-template-columns: repeat(3, 1fr); gap: 16px; margin-top: 16px; }
  .proj-card { background: #0d1117; border: 1px solid #30363d; border-radius: 8px; padding: 16px; text-align: center; }
  .proj-card .proj-label { font-size: 11px; color: #8b949e; text-transform: uppercase; letter-spacing: 0.5px; }
  .proj-card .proj-value { font-size: 28px; font-weight: 600; margin-top: 4px; }
  .proj-card .proj-range { font-size: 12px; color: #8b949e; margin-top: 4px; }
  .proj-card .proj-note { font-size: 11px; color: #6e7681; margin-top: 4px; font-style: italic; }
  .proj-value.negative { color: #f85149; }
  .proj-value.positive { color: #3fb950; }

  .source-note { font-size: 11px; color: #484f58; margin-top: 16px; text-align: center; }

  .graph-section { display: none; margin-top: 32px; }
  .graph-section.active { display: block; }
  .graph-section-header { font-size: 14px; color: #8b949e; text-transform: uppercase;
    letter-spacing: 0.5px; margin-bottom: 12px; padding-bottom: 8px;
    border-bottom: 1px solid #30363d; }
  .graph-container { background: #0d1117; border: 1px solid #30363d;
    border-radius: 8px; height: 560px; position: relative; margin-bottom: 12px; }
  .graph-legend { display: flex; flex-wrap: wrap; gap: 12px; margin-bottom: 12px; }
  .legend-item { display: flex; align-items: center; gap: 6px; font-size: 12px; color: #8b949e; }
  .legend-dot { width: 12px; height: 12px; border-radius: 50%; flex-shrink: 0; }
  .graph-empty { position: absolute; top: 50%; left: 50%;
    transform: translate(-50%, -50%); color: #484f58; font-size: 14px; text-align: center; }
  .graph-stats { font-size: 11px; color: #484f58; text-align: center; padding: 4px 0; }

  /* Entity type badge */
  .entity-type-badge { display: inline-flex; align-items: center; gap: 6px; padding: 4px 14px;
    border-radius: 12px; font-size: 13px; font-weight: 600; text-transform: uppercase;
    letter-spacing: 0.6px; margin-left: 12px; }
  .entity-type-badge.company  { background: rgba(88,166,255,0.15); color: #58a6ff; border: 1px solid rgba(88,166,255,0.3); }
  .entity-type-badge.person   { background: rgba(163,113,247,0.15); color: #a371f7; border: 1px solid rgba(163,113,247,0.3); }
  .entity-type-badge.vessel   { background: rgba(63,185,80,0.15); color: #3fb950; border: 1px solid rgba(63,185,80,0.3); }
  .entity-type-badge.sector   { background: rgba(240,136,62,0.15); color: #f0883e; border: 1px solid rgba(240,136,62,0.3); }

  /* Person profile */
  .person-header { display: flex; align-items: flex-start; gap: 20px; margin-bottom: 24px; }
  .person-avatar { width: 72px; height: 72px; border-radius: 50%; background: linear-gradient(135deg, #7B68EE 0%, #4A90D9 100%);
    display: flex; align-items: center; justify-content: center; font-size: 28px; flex-shrink: 0; }
  .person-meta { flex: 1; }
  .person-name { font-size: 22px; font-weight: 600; color: #e6edf3; margin-bottom: 4px; }
  .person-sub  { font-size: 13px; color: #8b949e; }
  .person-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; margin-bottom: 24px; }
  @media (max-width: 900px) { .person-grid { grid-template-columns: 1fr; } }
  .affiliations-list { list-style: none; }
  .affiliations-list li { padding: 8px 0; border-bottom: 1px solid #21262d; font-size: 13px;
    display: flex; justify-content: space-between; align-items: center; }
  .affiliations-list li:last-child { border-bottom: none; }
  .role-badge { font-size: 10px; padding: 2px 6px; border-radius: 10px; background: #21262d; color: #8b949e; }
  .events-list { list-style: none; }
  .events-list li { padding: 8px 0; border-bottom: 1px solid #21262d; font-size: 12px; color: #c9d1d9; }
  .events-list li:last-child { border-bottom: none; }
  .event-date { font-size: 11px; color: #484f58; margin-bottom: 2px; }
  .event-tone { font-size: 10px; padding: 1px 5px; border-radius: 8px; margin-left: 6px; }
  .event-tone.negative { background: rgba(248,81,73,0.15); color: #f85149; }
  .event-tone.positive { background: rgba(63,185,80,0.15); color: #3fb950; }

  /* Vessel profile */
  .vessel-grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 16px; margin-bottom: 24px; }
  .map-range-toggles { display: flex; gap: 8px; }
  .map-range-btn { background: #21262d; color: #8b949e; border: 1px solid #30363d; border-radius: 6px; padding: 6px 16px; font-size: 13px; cursor: pointer; transition: all 0.15s; }
  .map-range-btn:hover { border-color: #58a6ff; color: #58a6ff; }
  .map-range-btn.active { background: rgba(88,166,255,0.15); color: #58a6ff; border-color: #58a6ff; }
  @media (max-width: 900px) { .vessel-grid { grid-template-columns: 1fr 1fr; } }
  .vessel-stat { background: #0d1117; border: 1px solid #30363d; border-radius: 8px; padding: 14px; text-align: center; }
  .vessel-stat .v-label { font-size: 10px; color: #8b949e; text-transform: uppercase; letter-spacing: 0.5px; }
  .vessel-stat .v-value { font-size: 18px; font-weight: 600; color: #e6edf3; margin-top: 4px; }
  .route-table { width: 100%; border-collapse: collapse; font-size: 12px; }
  .route-table th { background: #21262d; color: #8b949e; font-size: 10px; text-transform: uppercase;
    letter-spacing: 0.5px; padding: 6px 10px; text-align: left; }
  .route-table td { padding: 6px 10px; border-bottom: 1px solid #21262d; color: #c9d1d9; font-family: monospace; }

  /* Sector panel */
  .sector-header { display: flex; align-items: center; gap: 12px; margin-bottom: 20px; }
  .sector-icon { font-size: 32px; }
  .sector-title { font-size: 22px; font-weight: 600; color: #e6edf3; }
  .sector-sub { font-size: 13px; color: #8b949e; margin-top: 2px; }
  .sector-companies { display: grid; grid-template-columns: repeat(auto-fill, minmax(220px, 1fr)); gap: 12px; margin-top: 16px; }
  .sector-company-card { background: #0d1117; border: 1px solid #30363d; border-radius: 8px; padding: 14px;
    cursor: pointer; transition: border-color 0.15s; }
  .sector-company-card:hover { border-color: #58a6ff; }
  .sector-company-card.sanctioned { border-color: rgba(248,81,73,0.4); background: rgba(248,81,73,0.05); }
  .sector-company-card .sc-name { font-size: 14px; font-weight: 500; color: #e6edf3; margin-bottom: 4px; }
  .sector-company-card .sc-ticker { font-size: 12px; color: #58a6ff; font-family: monospace; }
  .sector-company-card .sc-country { font-size: 11px; color: #484f58; margin-top: 4px; }
</style>
</head>
<body>

<div class="header">
  <h1>Economic Warfare OSINT — Sanctions Impact Projector</h1>
  <p>Analyze how sanctions affect publicly traded stocks using historical comparable data</p>
</div>

<div class="main">

  <div class="query-box">
    <input type="text" id="queryInput" placeholder="Search for a company, person, vessel, or sector..." />
    <div class="btn-row">
      <button class="btn btn-primary" id="analyzeBtn" onclick="startAnalysis()">Analyze</button>
      <button class="btn btn-secondary" id="deepAnalyzeBtn" onclick="startDeepAnalysis()" style="border-color:#58a6ff;color:#58a6ff;">Deep Analysis</button>
      <button class="btn btn-secondary" onclick="clearAll()">Clear</button>
      <span id="healthBadge"></span>
      <span id="entityTypeBadge" style="display:none;" class="entity-type-badge"></span>
    </div>
    <div class="examples">
      <span class="example-chip" data-ticker="BABA" onclick="runExample(this)">Sanction Alibaba (BABA)</span>
      <span class="example-chip" data-ticker="TSM" onclick="runExample(this)">What if we sanction TSMC?</span>
      <span class="example-chip" data-query="Viktor Vekselberg" onclick="runExample(this)">Viktor Vekselberg</span>
      <span class="example-chip" data-query="MSC Oscar" onclick="runExample(this)">Track vessel MSC Oscar</span>
      <span class="example-chip" data-query="Semiconductor" onclick="runExample(this)">Semiconductor sector</span>
      <span class="example-chip" data-query="Roman Abramovich" onclick="runExample(this)">Roman Abramovich</span>
    </div>
  </div>

  <div id="orchestratorPanel" style="display:none; margin-top:32px;">
    <div class="graph-section-header">Deep Analysis Results</div>
    <div class="info-card" style="margin-bottom:16px;">
      <h3>Executive Summary</h3>
      <div id="orchestratorSummary" style="line-height:1.6; font-size:14px; color:#c9d1d9;">—</div>
    </div>
    <div style="display:grid; grid-template-columns:1fr 1fr; gap:16px;">
      <div class="info-card">
        <h3>Key Findings</h3>
        <ul id="orchestratorFindings" class="events-list"><li style="color:#484f58">No findings</li></ul>
      </div>
      <div class="info-card">
        <h3>Recommendations</h3>
        <ul id="orchestratorRecommendations" class="events-list"><li style="color:#484f58">No recommendations</li></ul>
      </div>
    </div>
  </div>

  <div class="progress-panel" id="progressPanel">
    <h3><span class="spinner" id="progressSpinner"></span>Analyzing Sanctions Impact</h3>
    <div class="progress-log" id="progressLog"></div>
  </div>

  <div id="resultsPanel" style="display: none;">
    <!-- Target info cards -->
    <div class="impact-info" id="impactInfoCards"></div>

    <!-- Chart -->
    <div class="impact-chart-container">
      <canvas id="impactChart"></canvas>
    </div>

    <!-- Projection summary -->
    <div class="info-card" style="margin-bottom: 24px;">
      <h3>Projected Impact Summary</h3>
      <div class="projection-summary" id="projectionSummary"></div>
    </div>

    <!-- Comparables table -->
    <div class="info-card">
      <h3>Historical Comparable Cases <span style="font-size:11px; color:#484f58; text-transform:none; letter-spacing:0;">(click to toggle on chart)</span></h3>
      <table class="comparables-table" id="comparablesTable">
        <thead>
          <tr><th></th><th>Company</th><th>Ticker</th><th>Sanction Date</th><th>Description</th></tr>
        </thead>
        <tbody></tbody>
      </table>
    </div>

    <div class="source-note">
      Data sources: Yahoo Finance, OFAC SDN, Trade.gov Consolidated Screening List, OpenSanctions
    </div>
  </div>

  <div id="graphSection" class="graph-section">
    <div class="graph-section-header">Entity Relationship Graph</div>
    <div class="graph-legend">
      <span class="legend-item"><span class="legend-dot" style="background:#4A90D9"></span>Company</span>
      <span class="legend-item"><span class="legend-dot" style="background:#7B68EE"></span>Person</span>
      <span class="legend-item"><span class="legend-dot" style="background:#DC143C"></span>Government</span>
      <span class="legend-item"><span class="legend-dot" style="background:#F85149"></span>Sanctions</span>
      <span class="legend-item"><span class="legend-dot" style="background:#2E8B57"></span>Vessel</span>
      <span class="legend-item"><span class="legend-dot" style="background:#3FB950"></span>Sector</span>
    </div>
    <div class="graph-container" id="graphContainer">
      <div class="graph-empty" id="graphEmpty">Loading entity graph...</div>
    </div>
    <div class="graph-stats" id="graphStats"></div>
  </div>

  <!-- ── Person Profile Panel ── -->
  <div id="personPanel" style="display:none; margin-top:32px;">
    <div class="graph-section-header">Person Intelligence Profile</div>
    <div class="person-header">
      <div class="person-avatar" id="personAvatar">👤</div>
      <div class="person-meta">
        <div class="person-name" id="personName">—</div>
        <div class="person-sub" id="personSub">—</div>
        <div style="margin-top:8px;" id="personSanctionsBadge"></div>
      </div>
    </div>
    <div class="person-grid">
      <div class="info-card">
        <h3>Corporate Affiliations</h3>
        <ul class="affiliations-list" id="affiliationsList"><li style="color:#484f58">Loading...</li></ul>
      </div>
      <div class="info-card">
        <h3>Recent News Events (GDELT)</h3>
        <ul class="events-list" id="eventsList"><li style="color:#484f58">Loading...</li></ul>
      </div>
    </div>
    <div id="personGraphSection" class="graph-section active">
      <div class="graph-section-header">Network Graph</div>
      <div class="graph-legend">
        <span class="legend-item"><span class="legend-dot" style="background:#7B68EE"></span>Person</span>
        <span class="legend-item"><span class="legend-dot" style="background:#4A90D9"></span>Company</span>
        <span class="legend-item"><span class="legend-dot" style="background:#F85149"></span>Sanctions</span>
        <span class="legend-item"><span class="legend-dot" style="background:#F0883E"></span>Offshore</span>
      </div>
      <div class="graph-container" id="personGraphContainer">
        <div class="graph-empty" id="personGraphEmpty">Loading network...</div>
      </div>
      <div class="graph-stats" id="personGraphStats"></div>
    </div>
    <div class="source-note">Sources: OpenSanctions · OFAC SDN · OpenCorporates · ICIJ Offshore Leaks · GDELT</div>
  </div>

  <!-- ── Vessel Track Panel ── -->
  <div id="vesselPanel" style="display:none; margin-top:32px;">
    <div class="graph-section-header">Vessel Intelligence Profile</div>
    <div class="vessel-grid" id="vesselStats"></div>
    <div id="vesselSanctionsInfo" style="margin-bottom:12px;"></div>
    <div class="info-card" style="margin-bottom:24px;">
      <h3>Recent AIS Track</h3>
      <div style="max-height:280px; overflow-y:auto; border-radius:6px; border:1px solid #30363d;">
        <table class="route-table" style="margin:0;">
          <thead style="position:sticky; top:0; background:#161b22; z-index:1;"><tr><th>Lat</th><th>Lon</th><th>Speed</th></tr></thead>
          <tbody id="routeTableBody"></tbody>
        </table>
      </div>
    </div>
    <!-- AIS Route Map -->
    <div class="info-card" style="margin-bottom:24px;">
      <h3>AIS Route Map</h3>
      <div class="map-range-toggles">
        <button class="map-range-btn" data-range="24h" onclick="setMapRange('24h')">24 Hours</button>
        <button class="map-range-btn" data-range="1w" onclick="setMapRange('1w')">1 Week</button>
        <button class="map-range-btn active" data-range="1m" onclick="setMapRange('1m')">1 Month</button>
      </div>
      <div id="vesselMap" style="height:420px; border-radius:8px; margin-top:12px; background:#0d1117; border:1px solid #30363d;"></div>
      <div id="vesselMapEmpty" class="graph-empty" style="position:relative; top:-220px; pointer-events:none;">No AIS position data available</div>
    </div>

    <!-- Risk Assessment Narrative -->
    <div class="info-card" id="vesselNarrativeCard" style="display:none; margin-bottom:24px;">
      <h3>Risk Assessment</h3>
      <p id="vesselNarrativeText" style="color:#e6edf3; line-height:1.6; margin:0;"></p>
    </div>

    <!-- Countries Visited -->
    <div class="info-card" id="vesselCountriesCard" style="display:none; margin-bottom:24px;">
      <h3>Countries / Regions Visited</h3>
      <div id="vesselCountriesContent" style="display:flex; flex-wrap:wrap; gap:6px; margin-top:8px;"></div>
    </div>

    <!-- Beneficial Ownership Chain (Sayari) -->
    <div class="info-card" id="ownershipChainCard" style="display:none; margin-bottom:24px;">
      <h3>Beneficial Ownership Chain <span style="font-size:11px; color:#58a6ff; font-weight:normal; text-transform:none;">(Sayari Graph)</span></h3>
      <div id="ownershipChainContent" style="margin-top:8px;"></div>
    </div>

    <!-- Trade Activity (Sayari) -->
    <div class="info-card" id="tradeActivityCard" style="display:none; margin-bottom:24px;">
      <h3>Trade Activity <span style="font-size:11px; color:#58a6ff; font-weight:normal; text-transform:none;">(Sayari Graph)</span></h3>
      <div id="tradeActivityContent"></div>
    </div>

    <!-- Trade Flow Sankey -->
    <div class="info-card" id="tradeFlowCard" style="display:none; margin-bottom:24px;">
      <h3>Trade Flow Diagram</h3>
      <div style="position:relative; height:500px;">
        <canvas id="tradeFlowSankey"></canvas>
      </div>
    </div>

    <!-- Tabbed Network Graph -->
    <div id="vesselGraphSection" class="graph-section active">
      <div class="graph-section-header">Network Analysis</div>
      <div style="display:flex; gap:8px; margin-bottom:12px;">
        <button class="map-range-btn active" id="tabOwnership" onclick="switchVesselTab('ownership')">Ownership &amp; Sanctions</button>
        <button class="map-range-btn" id="tabTrade" onclick="switchVesselTab('trade')">Trade Network</button>
      </div>
      <div class="graph-legend" id="vesselGraphLegend">
        <span class="legend-item"><span class="legend-dot" style="background:#3fb950"></span>Vessel</span>
        <span class="legend-item"><span class="legend-dot" style="background:#58a6ff"></span>Company</span>
        <span class="legend-item"><span class="legend-dot" style="background:#a371f7"></span>Person/UBO</span>
        <span class="legend-item"><span class="legend-dot" style="background:#DC143C"></span>Flag State</span>
        <span class="legend-item"><span class="legend-dot" style="background:#F85149"></span>Sanctions/Risk</span>
      </div>
      <div class="graph-container" id="vesselGraphContainer">
        <div class="graph-empty" id="vesselGraphEmpty">Loading network...</div>
      </div>
    </div>
    <div class="source-note" id="vesselSourceNote">Sources: Datalastic AIS · OFAC SDN</div>
  </div>

  <!-- ── Sector Analysis Panel ── -->
  <div id="sectorPanel" style="display:none; margin-top:32px;">
    <div class="graph-section-header">Sector Intelligence</div>
    <div class="sector-header">
      <div class="sector-icon">🏭</div>
      <div>
        <div class="sector-title" id="sectorTitle">—</div>
        <div class="sector-sub" id="sectorSub">—</div>
      </div>
    </div>
    <div style="display:grid; grid-template-columns:repeat(3,1fr); gap:16px; margin-bottom:24px;">
      <div class="info-card" style="text-align:center;">
        <h3>Key Players</h3>
        <div class="value" id="sectorCompanyCount">—</div>
      </div>
      <div class="info-card" style="text-align:center;">
        <h3>Sanctioned Entities</h3>
        <div class="value" id="sectorSanctionCount" style="color:#f85149;">—</div>
      </div>
      <div class="info-card" style="text-align:center;">
        <h3>Sanction Coverage</h3>
        <div class="value" id="sectorCoverage">—</div>
      </div>
    </div>
    <div class="info-card" style="margin-bottom:24px;">
      <h3>Key Players</h3>
      <div class="sector-companies" id="sectorCompanies"></div>
    </div>
    <div id="sectorGraphSection" class="graph-section active">
      <div class="graph-section-header">Sector Network</div>
      <div class="graph-legend">
        <span class="legend-item"><span class="legend-dot" style="background:#3FB950"></span>Sector</span>
        <span class="legend-item"><span class="legend-dot" style="background:#4A90D9"></span>Company</span>
        <span class="legend-item"><span class="legend-dot" style="background:#F85149"></span>Sanctioned</span>
      </div>
      <div class="graph-container" id="sectorGraphContainer">
        <div class="graph-empty" id="sectorGraphEmpty">Loading sector graph...</div>
      </div>
      <div class="graph-stats" id="sectorGraphStats"></div>
    </div>
    <div class="source-note">Sources: OFAC SDN · OpenSanctions</div>
  </div>

</div>

<script>
let impactChart = null;
let lastData = null;
let visNetwork = null;
let personNetwork = null;
let vesselNetwork = null;
let sectorNetwork = null;
let orchestratorProgressCursor = 0;

const VIS_OPTS = {
  physics: {
    solver: 'repulsion',
    repulsion: { nodeDistance: 180, centralGravity: 0.15, springLength: 200, springConstant: 0.04, damping: 0.09 },
    stabilization: { iterations: 300 },
  },
  nodes: {
    shape: 'dot', size: 18,
    font: { color: '#c9d1d9', size: 12, strokeWidth: 3, strokeColor: '#0d1117' },
    borderWidth: 2,
    color: { border: '#30363d', highlight: { border: '#58a6ff' }, hover: { border: '#58a6ff' } },
  },
  edges: {
    font: { color: '#8b949e', size: 10, align: 'middle', strokeWidth: 2, strokeColor: '#0d1117' },
    color: { color: '#58a6ff', highlight: '#ffffff', opacity: 0.6 },
    width: 2,
    smooth: { type: 'continuous' },
    arrows: { to: { enabled: true, scaleFactor: 0.5 } },
  },
  interaction: { hover: true, tooltipDelay: 150 },
  layout: { randomSeed: 42 },
};

function renderVisGraph(containerId, emptyId, statsId, nodes, edges, networkRef) {
  const container = document.getElementById(containerId);
  const emptyEl   = document.getElementById(emptyId);
  const statsEl   = document.getElementById(statsId);
  if (!nodes || nodes.length === 0) {
    if (emptyEl) { emptyEl.style.display = 'block'; emptyEl.textContent = 'No entity relationships found'; }
    return null;
  }
  if (emptyEl) emptyEl.style.display = 'none';
  const net = new vis.Network(container, {
    nodes: new vis.DataSet(nodes),
    edges: new vis.DataSet(edges),
  }, VIS_OPTS);
  net.once('stabilized', () => net.fit({ animation: { duration: 400, easingFunction: 'easeInOutQuad' } }));
  if (statsEl) statsEl.textContent = `${nodes.length} entities · ${edges.length} relationships`;
  return net;
}

// Known company name → ticker map for natural language input
const KNOWN_MAP = {
  'alibaba': 'BABA', 'baba': 'BABA',
  'smic': '0981.HK',
  'tsmc': 'TSM', 'tsm': 'TSM', 'taiwan semiconductor': 'TSM',
  'china mobile': '0941.HK',
  'hikvision': '002415.SZ',
  'xiaomi': '1810.HK',
  'zte': '0763.HK',
  'baidu': 'BIDU', 'bidu': 'BIDU',
  'nio': 'NIO',
  'asml': 'ASML',
  'intel': 'INTC', 'intc': 'INTC',
  'micron': 'MU',
  'huawei': 'BABA',  // not public, use Alibaba as proxy
  'tencent': 'TME', 'tme': 'TME',
  'bilibili': 'BILI', 'bili': 'BILI',
  'pdd': 'PDD', 'pinduoduo': 'PDD',
  'kweb': 'KWEB',
  'full truck': 'YMM', 'ymm': 'YMM',
};

function extractTicker(input) {
  const lower = input.toLowerCase().trim();
  // Check known map first
  for (const [name, ticker] of Object.entries(KNOWN_MAP)) {
    if (lower.includes(name)) return ticker;
  }
  // Try to find an uppercase ticker-like pattern (1-5 letters, optionally .XX)
  const match = input.match(/\\b([A-Z]{1,5}(?:\\.[A-Z]{1,2})?)\\b/);
  return match ? match[1] : input.trim().toUpperCase();
}

// Health check
fetch('/api/health').then(r => r.json()).then(data => {
  const badge = document.getElementById('healthBadge');
  if (data.status === 'ok') {
    badge.innerHTML = '<span class="status-badge ok">API Connected</span>';
  } else {
    badge.innerHTML = '<span class="status-badge error">' + (data.issues || []).join(', ') + '</span>';
  }
}).catch(() => {});

function runExample(el) {
  var query = el.dataset.query || el.textContent;
  document.getElementById('queryInput').value = query;
  startAnalysis(el.dataset.ticker || null);
}

function clearAll() {
  document.getElementById('resultsPanel').style.display = 'none';
  document.getElementById('progressPanel').classList.remove('active');
  document.getElementById('progressLog').innerHTML = '';
  document.getElementById('queryInput').value = '';
  if (impactChart) { impactChart.destroy(); impactChart = null; }
  lastData = null;
  document.getElementById('graphSection').classList.remove('active');
  document.getElementById('graphEmpty').style.display = 'block';
  document.getElementById('graphEmpty').textContent = 'Loading entity graph...';
  document.getElementById('graphStats').textContent = '';
  if (visNetwork) { visNetwork.destroy(); visNetwork = null; }
  if (personNetwork) { personNetwork.destroy(); personNetwork = null; }
  if (vesselNetwork) { vesselNetwork.destroy(); vesselNetwork = null; }
  if (vesselMapInstance) { vesselMapInstance.remove(); vesselMapInstance = null; }
  vesselRouteData = [];
  if (sectorNetwork) { sectorNetwork.destroy(); sectorNetwork = null; }
  document.getElementById('personPanel').style.display = 'none';
  document.getElementById('vesselPanel').style.display = 'none';
  document.getElementById('sectorPanel').style.display = 'none';
  document.getElementById('orchestratorPanel').style.display = 'none';
  orchestratorProgressCursor = 0;
  const badge = document.getElementById('entityTypeBadge');
  badge.style.display = 'none';
  badge.textContent = '';
  badge.className = 'entity-type-badge';
}

async function loadEntityGraph(query) {
  document.getElementById('graphSection').classList.add('active');
  document.getElementById('graphEmpty').style.display = 'block';
  document.getElementById('graphEmpty').textContent = 'Loading entity graph...';
  document.getElementById('graphStats').textContent = '';
  if (visNetwork) { visNetwork.destroy(); visNetwork = null; }
  try {
    const resp = await fetch('/api/entity-graph', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ query }),
    });
    if (!resp.ok) { document.getElementById('graphEmpty').textContent = 'Graph unavailable'; return; }
    const data = await resp.json();
    if (!data.nodes || data.nodes.length === 0) {
      document.getElementById('graphEmpty').textContent = 'No entity relationships found';
      return;
    }
    document.getElementById('graphEmpty').style.display = 'none';
    const container = document.getElementById('graphContainer');
    const options = {
      physics: {
        solver: 'repulsion',
        repulsion: { nodeDistance: 180, centralGravity: 0.15, springLength: 200, springConstant: 0.04, damping: 0.09 },
        stabilization: { iterations: 300 },
      },
      nodes: {
        shape: 'dot', size: 18,
        font: { color: '#c9d1d9', size: 12, strokeWidth: 3, strokeColor: '#0d1117' },
        borderWidth: 2,
        color: { border: '#30363d', highlight: { border: '#58a6ff' }, hover: { border: '#58a6ff' } },
      },
      edges: {
        font: { color: '#8b949e', size: 10, align: 'middle', strokeWidth: 2, strokeColor: '#0d1117' },
        color: { color: '#58a6ff', highlight: '#ffffff', opacity: 0.6 },
        width: 2,
        smooth: { type: 'continuous' },
        arrows: { to: { enabled: true, scaleFactor: 0.5 } },
      },
      interaction: { hover: true, tooltipDelay: 150 },
      layout: { randomSeed: 42 },
    };
    visNetwork = new vis.Network(container, {
      nodes: new vis.DataSet(data.nodes),
      edges: new vis.DataSet(data.edges),
    }, options);
    visNetwork.once('stabilized', () => visNetwork.fit({ animation: { duration: 400, easingFunction: 'easeInOutQuad' } }));
    document.getElementById('graphStats').textContent =
      `${data.meta.node_count} entities · ${data.meta.edge_count} relationships`;
  } catch(e) {
    const el = document.getElementById('graphEmpty');
    el.style.display = 'block';
    el.textContent = 'Error: ' + e.message;
  }
}

async function startAnalysis(tickerOverride) {
  const raw = document.getElementById('queryInput').value.trim();
  if (!raw && !tickerOverride) return;

  const btn = document.getElementById('analyzeBtn');
  btn.disabled = true;
  // Reset panels without wiping the input
  document.getElementById('resultsPanel').style.display = 'none';
  document.getElementById('progressPanel').classList.remove('active');
  document.getElementById('progressLog').innerHTML = '';
  if (impactChart) { impactChart.destroy(); impactChart = null; }
  lastData = null;
  document.getElementById('graphSection').classList.remove('active');
  if (visNetwork) { visNetwork.destroy(); visNetwork = null; }
  if (personNetwork) { personNetwork.destroy(); personNetwork = null; }
  if (vesselNetwork) { vesselNetwork.destroy(); vesselNetwork = null; }
  if (sectorNetwork) { sectorNetwork.destroy(); sectorNetwork = null; }
  document.getElementById('personPanel').style.display = 'none';
  document.getElementById('vesselPanel').style.display = 'none';
  document.getElementById('sectorPanel').style.display = 'none';
  document.getElementById('orchestratorPanel').style.display = 'none';
  const deepBtn = document.getElementById('deepAnalyzeBtn');
  if (deepBtn) deepBtn.disabled = true;
  const _badge = document.getElementById('entityTypeBadge');
  _badge.style.display = 'none'; _badge.className = 'entity-type-badge';

  const panel = document.getElementById('progressPanel');
  panel.classList.add('active');
  document.getElementById('progressLog').innerHTML = '';
  document.getElementById('progressSpinner').style.display = 'inline-block';

  try {
    // Step 1: Resolve entity type
    addProgress('Classifying entity type...', 'step');
    let entityType = 'company';
    let entityName = tickerOverride || raw;

    if (!tickerOverride) {
      try {
        const resolveResp = await fetch('/api/resolve-entity', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ query: raw }),
        });
        if (resolveResp.ok) {
          const resolution = await resolveResp.json();
          entityType = resolution.entity_type;
          entityName = resolution.entity_name;
          addProgress('Detected: ' + entityType.toUpperCase() + ' — ' + entityName, 'step');
        }
      } catch(e) {
        addProgress('Entity resolution failed, defaulting to company', 'step');
      }
    }

    // Show entity type badge
    const badge = document.getElementById('entityTypeBadge');
    const icons = { company: '🏢', person: '👤', vessel: '🚢', sector: '🏭' };
    badge.textContent = (icons[entityType] || '') + ' ' + entityType;
    badge.className = 'entity-type-badge ' + entityType;
    badge.style.display = 'inline-flex';

    // Step 2: Route to entity-specific handler
    if (entityType === 'orchestrator') {
      // Hand off to deep analysis pipeline — manages its own UI state
      document.getElementById('progressSpinner').style.display = 'none';
      btn.disabled = false;
      if (deepBtn) deepBtn.disabled = false;
      await startDeepAnalysis();
      return;
    } else if (entityType === 'person') {
      await runPersonAnalysis(entityName);
    } else if (entityType === 'vessel') {
      await runVesselAnalysis(entityName);
    } else if (entityType === 'sector') {
      await runSectorAnalysis(entityName);
    } else {
      // company — existing flow
      const ticker = tickerOverride || extractTicker(raw);
      await runCompanyAnalysis(ticker, entityName);
    }

  } catch (e) {
    addProgress('Error: ' + e.message, 'error');
  }

  document.getElementById('progressSpinner').style.display = 'none';
  btn.disabled = false;
  if (deepBtn) deepBtn.disabled = false;
}

async function startDeepAnalysis() {
  const raw = document.getElementById('queryInput').value.trim();
  if (!raw) return;

  const analyzeBtn = document.getElementById('analyzeBtn');
  const deepBtn = document.getElementById('deepAnalyzeBtn');
  analyzeBtn.disabled = true;
  deepBtn.disabled = true;

  document.getElementById('resultsPanel').style.display = 'none';
  document.getElementById('personPanel').style.display = 'none';
  document.getElementById('vesselPanel').style.display = 'none';
  document.getElementById('sectorPanel').style.display = 'none';
  document.getElementById('orchestratorPanel').style.display = 'none';
  document.getElementById('graphSection').classList.remove('active');
  const badge = document.getElementById('entityTypeBadge');
  badge.textContent = 'Deep Analysis';
  badge.className = 'entity-type-badge sector';
  badge.style.display = 'inline-flex';

  const panel = document.getElementById('progressPanel');
  panel.classList.add('active');
  document.getElementById('progressLog').innerHTML = '';
  document.getElementById('progressSpinner').style.display = 'inline-block';
  orchestratorProgressCursor = 0;

  try {
    addProgress('Submitting query to orchestrator...', 'step');
    const startResp = await fetch('/api/analyze', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ query: raw }),
    });
    if (!startResp.ok) {
      const err = await startResp.json();
      throw new Error(err.detail || 'Failed to start analysis');
    }
    const started = await startResp.json();
    addProgress('Pipeline started: ' + started.analysis_id, 'step');

    let completed = false;
    let attempts = 0;
    while (!completed && attempts < 180) {
      attempts += 1;
      await new Promise((r) => setTimeout(r, 2000));

      const pollResp = await fetch('/api/analyze/' + started.analysis_id);
      if (!pollResp.ok) {
        throw new Error('Polling failed with HTTP ' + pollResp.status);
      }
      const status = await pollResp.json();

      const progress = Array.isArray(status.progress) ? status.progress : [];
      while (orchestratorProgressCursor < progress.length) {
        const msg = String(progress[orchestratorProgressCursor] || '');
        const kind = msg.toLowerCase().startsWith('error') ? 'error'
          : (msg === 'Done.' || msg.toLowerCase().includes('complete') ? 'done' : 'step');
        addProgress(msg, kind);
        orchestratorProgressCursor += 1;
      }

      if (status.status === 'completed' && status.result) {
        renderOrchestratorResults(status.result);
        completed = true;
      } else if (status.status === 'failed') {
        throw new Error(status.error || 'Deep analysis failed');
      }
    }

    if (!completed) {
      throw new Error('Deep analysis timed out while waiting for completion');
    }

  } catch (e) {
    addProgress('Error: ' + e.message, 'error');
  } finally {
    document.getElementById('progressSpinner').style.display = 'none';
    analyzeBtn.disabled = false;
    deepBtn.disabled = false;
  }
}

// ── Company (existing flow) ──────────────────────────────────────────────────
async function runCompanyAnalysis(ticker, entityName) {
  addProgress('Checking sanctions status (OFAC, OpenSanctions, Trade.gov CSL)...', 'step');
  addProgress('Fetching historical comparable data...', 'step');

  const resp = await fetch('/api/sanctions-impact', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ ticker }),
  });

  if (!resp.ok) {
    const err = await resp.json();
    addProgress('Error: ' + (err.detail || 'Request failed'), 'error');
    return;
  }

  const data = await resp.json();
  lastData = data;
  addProgress('Found ' + data.metadata.comparable_count + ' comparable sanctions cases', 'step');
  addProgress('Computing projection with confidence interval...', 'step');
  addProgress('Done!', 'done');
  document.getElementById('progressSpinner').style.display = 'none';

  renderResults(data);
  loadEntityGraph(ticker || entityName);
}

// ── Person (insider-threat style) ────────────────────────────────────────────
async function runPersonAnalysis(name) {
  addProgress('Searching OFAC SDN + OpenSanctions (person schema)...', 'step');
  addProgress('Looking up corporate affiliations (OpenCorporates)...', 'step');
  addProgress('Pulling GDELT news events...', 'step');

  const resp = await fetch('/api/person-profile', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name }),
  });
  if (!resp.ok) { addProgress('Error fetching person profile', 'error'); return; }

  const data = await resp.json();
  addProgress('Done!', 'done');
  document.getElementById('progressSpinner').style.display = 'none';

  renderPersonProfile(data);
}

function renderPersonProfile(data) {
  document.getElementById('personPanel').style.display = 'block';

  document.getElementById('personName').textContent = data.name;
  const sub = [
    data.nationality ? '🌍 ' + data.nationality : null,
    data.dob ? '🎂 DOB: ' + data.dob : null,
    data.aliases && data.aliases.length ? 'AKA: ' + data.aliases.slice(0,3).join(', ') : null,
  ].filter(Boolean).join('   ·   ') || 'No biographical data';
  document.getElementById('personSub').textContent = sub;

  const isSanctioned = data.is_sanctioned;
  document.getElementById('personSanctionsBadge').innerHTML = isSanctioned
    ? '<span class="sanctions-badge sanctioned">🚨 SANCTIONED</span>' +
      (data.sanction_programs.length ? ' <span style="font-size:11px;color:#8b949e">' + data.sanction_programs.join(', ') + '</span>' : '')
    : '<span class="sanctions-badge clear">✓ No Active Sanctions</span>';

  // Affiliations
  const affList = document.getElementById('affiliationsList');
  if (data.affiliations && data.affiliations.length) {
    affList.innerHTML = data.affiliations.map(a =>
      '<li>' +
      '<span>' + a.company + '</span>' +
      '<span><span class="role-badge">' + (a.role || 'Officer') + '</span>' +
      (a.active === false ? ' <span style="font-size:10px;color:#484f58">(inactive)</span>' : '') +
      '</span></li>'
    ).join('');
  } else {
    affList.innerHTML = '<li style="color:#484f58">No corporate affiliations found</li>';
  }

  // Events
  const evList = document.getElementById('eventsList');
  if (data.recent_events && data.recent_events.length) {
    evList.innerHTML = data.recent_events.map(ev => {
      const tone = parseFloat(ev.tone);
      const toneClass = isNaN(tone) ? '' : (tone < 0 ? 'negative' : 'positive');
      const toneLabel = isNaN(tone) ? '' : '<span class="event-tone ' + toneClass + '">' + (tone >= 0 ? '+' : '') + tone.toFixed(1) + '</span>';
      const url = ev.source ? '<a href="' + ev.source + '" target="_blank" style="color:#58a6ff;text-decoration:none;">↗</a>' : '';
      return '<li><div class="event-date">' + (ev.date || '').replace('T',' ').slice(0,16) + toneLabel + ' ' + url + '</div>' +
             (ev.title || ev.source || '').slice(0, 100) + '</li>';
    }).join('');
  } else {
    evList.innerHTML = '<li style="color:#484f58">No recent news events found</li>';
  }

  // Graph
  const g = data.graph || {};
  personNetwork = renderVisGraph('personGraphContainer', 'personGraphEmpty', 'personGraphStats',
    g.nodes, g.edges, personNetwork);
}

// ── Vessel ───────────────────────────────────────────────────────────────────
async function runVesselAnalysis(query) {
  addProgress('Querying Datalastic AIS for vessel position...', 'step');
  addProgress('Checking OFAC SDN for vessel sanctions...', 'step');

  const resp = await fetch('/api/vessel-track', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ query }),
  });
  if (!resp.ok) { addProgress('Error fetching vessel data', 'error'); return; }

  const data = await resp.json();
  addProgress('Done!', 'done');
  document.getElementById('progressSpinner').style.display = 'none';

  renderVesselProfile(data);
}

function renderVesselProfile(data) {
  document.getElementById('vesselPanel').style.display = 'block';
  const v = data.vessel || {};

  // Sanctions as a stat card instead of separate section
  var sancValue = data.is_sanctioned
    ? '<span style="color:#f85149;font-weight:600;">SANCTIONED</span>'
    : '<span style="color:#3fb950;">Clear</span>';

  const stats = [
    { label: 'Vessel Name', value: v.name || '—' },
    { label: 'IMO', value: v.imo || '—' },
    { label: 'MMSI', value: v.mmsi || '—' },
    { label: 'Flag', value: v.flag || '—' },
    { label: 'Type', value: v.vessel_type || '—' },
    { label: 'OFAC Status', value: sancValue, raw: true },
    { label: 'Speed', value: v.speed != null ? v.speed + ' kn' : '—' },
    { label: 'Destination', value: v.destination || '—' },
    { label: 'Owner', value: data.owner_name || v.owner || '—' },
  ];
  document.getElementById('vesselStats').innerHTML = stats.map(function(s) {
    return '<div class="vessel-stat"><div class="v-label">' + s.label + '</div><div class="v-value">' + s.value + '</div></div>';
  }).join('');

  // Sanctions detail (collapse into info line under stats if sanctioned)
  var sEl = document.getElementById('vesselSanctionsInfo');
  if (data.is_sanctioned && data.sanctions_matches && data.sanctions_matches.length) {
    sEl.innerHTML = '<ul style="margin:0;list-style:none;padding:0;">' +
      data.sanctions_matches.slice(0,3).map(function(m) {
        return '<li style="font-size:12px;color:#f85149;padding:2px 0;">' + m.name + ' (score: ' + (m.score || 0).toFixed(2) + ') — ' + (m.programs || []).join(', ') + '</li>';
      }).join('') + '</ul>';
  } else {
    sEl.innerHTML = '';
  }

  // Route history table (last 12 points)
  var tbody = document.getElementById('routeTableBody');
  var pts = (data.route_history || []).slice(-12);
  tbody.innerHTML = pts.length
    ? pts.map(function(p) { return '<tr><td>' + (p.lat || '—') + '</td><td>' + (p.lon || '—') + '</td><td>' + (p.speed != null ? p.speed + ' kn' : '—') + '</td></tr>'; }).join('')
    : '<tr><td colspan="3" style="color:#484f58">No AIS track data available</td></tr>';

  // AIS Route Map
  try { initVesselMap(data.route_history, v); } catch(e) { console.error('Map init error:', e); }

  // --- Narrative ---
  var narCard = document.getElementById('vesselNarrativeCard');
  if (data.narrative) {
    narCard.style.display = 'block';
    document.getElementById('vesselNarrativeText').textContent = data.narrative;
  } else {
    narCard.style.display = 'none';
  }

  // --- Countries Visited ---
  var cCard = document.getElementById('vesselCountriesCard');
  var countries = data.countries_visited || [];
  if (countries.length) {
    cCard.style.display = 'block';
    document.getElementById('vesselCountriesContent').innerHTML = countries.map(function(c) {
      var isOcean = c.charAt(0) === '(';
      var bg = isOcean ? '#1c2129' : '#0d2137';
      var border = isOcean ? '#30363d' : '#1f6feb';
      var color = isOcean ? '#8b949e' : '#58a6ff';
      return '<span style="background:' + bg + ';border:1px solid ' + border + ';border-radius:12px;padding:4px 12px;font-size:12px;color:' + color + ';">' + c + '</span>';
    }).join('');
  } else {
    cCard.style.display = 'none';
  }

  // --- Ownership Chain (Sayari) ---
  var chainCard = document.getElementById('ownershipChainCard');
  var chainContent = document.getElementById('ownershipChainContent');
  var chain = data.ownership_chain || [];
  if (chain.length) {
    chainCard.style.display = 'block';
    var relLabels = {
      registered_owner: 'Registered Owner', owner: 'Owner', beneficial_owner: 'Beneficial Owner',
      operator: 'Operator', builder: 'Builder', manager: 'Manager', ism_manager: 'ISM Manager',
      charterer: 'Charterer', technical_manager: 'Technical Manager', commercial_manager: 'Commercial Manager',
      group_beneficial_owner: 'Group Beneficial Owner', original_ship_orderer: 'Original Ship Orderer',
      management_company: 'Management Company'
    };
    chainContent.innerHTML = chain.map(function(link) {
      var badges = '';
      if (link.is_sanctioned) badges += '<span class="sanctions-badge sanctioned" style="font-size:10px;padding:2px 6px;margin-left:8px;">SANCTIONED</span>';
      if (link.is_pep) badges += '<span style="display:inline-block;padding:2px 6px;border-radius:12px;font-size:10px;background:rgba(184,134,11,0.15);color:#b8860b;border:1px solid rgba(184,134,11,0.3);margin-left:8px;">PEP</span>';
      var pct = link.ownership_percentage ? ' (' + link.ownership_percentage + '%)' : '';
      var icon = link.entity_type === 'person' ? '&#x1F464;' : '&#x1F3E2;';
      var color = link.entity_type === 'person' ? '#a371f7' : '#4A90D9';
      var rel = relLabels[link.relationship_type] || link.relationship_type || 'Related';
      var indent = ((link.depth || 1) - 1) * 24;
      return '<div style="padding:10px;border:1px solid #30363d;border-radius:6px;margin:2px 0;background:#0d1117;margin-left:' + indent + 'px;">' +
        '<span style="color:' + color + ';">' + icon + '</span> <strong>' + link.name + '</strong>' + pct + badges +
        '<div style="font-size:11px;color:#8b949e;">' + rel + ' &middot; ' + (link.country || 'Unknown') + '</div></div>';
    }).join('');
  } else {
    chainCard.style.display = 'none';
  }

  // --- Trade Activity (Sayari) ---
  var tradeCard = document.getElementById('tradeActivityCard');
  var tradeContent = document.getElementById('tradeActivityContent');
  var ta = data.trade_activity;
  if (ta && ta.records && ta.records.length) {
    tradeCard.style.display = 'block';
    var tradeHtml = '<table class="route-table"><thead><tr><th>Date</th><th>From</th><th>To</th><th>Commodity</th></tr></thead><tbody>';
    ta.records.slice(0, 10).forEach(function(r) {
      tradeHtml += '<tr><td>' + (r.date || '&mdash;') + '</td><td>' + (r.departure_country || '&mdash;') + '</td><td>' + (r.arrival_country || '&mdash;') + '</td><td style="font-size:11px;">' + (r.hs_description || r.hs_code || '&mdash;') + '</td></tr>';
    });
    tradeHtml += '</tbody></table>';
    if (ta.top_hs_codes && ta.top_hs_codes.length) {
      tradeHtml += '<div style="margin-top:12px;"><span style="font-size:11px;color:#8b949e;text-transform:uppercase;letter-spacing:0.5px;">Top Commodities</span><div style="display:flex;flex-wrap:wrap;gap:6px;margin-top:6px;">';
      ta.top_hs_codes.forEach(function(hs) {
        tradeHtml += '<span style="background:#1c2129;border:1px solid #30363d;border-radius:12px;padding:3px 10px;font-size:11px;color:#8b949e;">' + (hs.description || hs.code) + '</span>';
      });
      tradeHtml += '</div></div>';
    }
    if (ta.trade_countries && ta.trade_countries.length) {
      tradeHtml += '<div style="margin-top:12px;"><span style="font-size:11px;color:#8b949e;text-transform:uppercase;letter-spacing:0.5px;">Trade Countries</span><div style="display:flex;flex-wrap:wrap;gap:6px;margin-top:6px;">';
      ta.trade_countries.forEach(function(c) {
        tradeHtml += '<span style="background:#1c2129;border:1px solid #30363d;border-radius:12px;padding:3px 10px;font-size:11px;color:#58a6ff;">' + c + '</span>';
      });
      tradeHtml += '</div></div>';
    }
    tradeContent.innerHTML = tradeHtml;
  } else {
    tradeCard.style.display = 'none';
  }

  // --- Sankey Trade Flow ---
  var flowCard = document.getElementById('tradeFlowCard');
  var sankey = (ta && ta.sankey_flows) || [];
  if (sankey.length >= 6 && typeof Chart !== 'undefined') {
    flowCard.style.display = 'block';
    var ctx = document.getElementById('tradeFlowSankey');
    if (window._vesselSankey) { window._vesselSankey.destroy(); }
    window._vesselSankey = new Chart(ctx, {
      type: 'sankey',
      data: {
        datasets: [{
          data: sankey.map(function(f) { return {from: f.from, to: f.to, flow: f.flow}; }),
          colorFrom: function(c) {
            var key = c.dataset.data[c.dataIndex].from;
            return key.indexOf('(origin)') >= 0 ? '#58a6ff' : '#f0883e';
          },
          colorTo: function(c) {
            var key = c.dataset.data[c.dataIndex].to;
            return key.indexOf('(dest)') >= 0 ? '#3fb950' : '#f0883e';
          },
          colorMode: 'gradient',
          labels: (ta && ta.sankey_labels) || {},
          size: 'max',
          nodeWidth: 12,
          nodePadding: 24,
        }]
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: { legend: { display: false } },
      }
    });
  } else {
    flowCard.style.display = 'none';
  }

  // --- Sources ---
  var sources = data.sources || ['Datalastic AIS', 'OFAC SDN'];
  document.getElementById('vesselSourceNote').textContent = 'Sources: ' + sources.join(' · ');

  // --- Tabbed Graphs ---
  vesselOwnershipData = data.graph || {nodes: [], edges: []};
  vesselTradeData = data.trade_graph || {nodes: [], edges: []};
  switchVesselTab('ownership');
}

var vesselOwnershipData = null;
var vesselTradeData = null;

function switchVesselTab(tab) {
  var ownerBtn = document.getElementById('tabOwnership');
  var tradeBtn = document.getElementById('tabTrade');
  ownerBtn.className = 'map-range-btn' + (tab === 'ownership' ? ' active' : '');
  tradeBtn.className = 'map-range-btn' + (tab === 'trade' ? ' active' : '');
  var graphData = tab === 'ownership' ? vesselOwnershipData : vesselTradeData;
  if (graphData && graphData.nodes && graphData.nodes.length) {
    vesselNetwork = renderVisGraph('vesselGraphContainer', 'vesselGraphEmpty', null,
      graphData.nodes, graphData.edges, vesselNetwork);
  } else {
    document.getElementById('vesselGraphEmpty').textContent = 'No ' + tab + ' data available';
    document.getElementById('vesselGraphEmpty').style.display = 'block';
  }
  // Update legend for trade tab
  var legend = document.getElementById('vesselGraphLegend');
  if (tab === 'trade') {
    legend.innerHTML =
      '<span class="legend-item"><span class="legend-dot" style="background:#3fb950"></span>Vessel</span>' +
      '<span class="legend-item"><span class="legend-dot" style="background:#58a6ff"></span>Trade Partner</span>' +
      '<span class="legend-item"><span class="legend-dot" style="background:#F85149"></span>Risk Flagged</span>';
  } else {
    legend.innerHTML =
      '<span class="legend-item"><span class="legend-dot" style="background:#3fb950"></span>Vessel</span>' +
      '<span class="legend-item"><span class="legend-dot" style="background:#58a6ff"></span>Company</span>' +
      '<span class="legend-item"><span class="legend-dot" style="background:#a371f7"></span>Person/UBO</span>' +
      '<span class="legend-item"><span class="legend-dot" style="background:#DC143C"></span>Flag State</span>' +
      '<span class="legend-item"><span class="legend-dot" style="background:#F85149"></span>Sanctions</span>';
  }
}

// ── Vessel AIS Map ───────────────────────────────────────────────────────────
let vesselMapInstance = null;
let vesselRouteLayer = null;
let vesselMarkers = null;
let vesselRouteData = [];
let currentMapRange = '1m';

function initVesselMap(routeHistory, vesselDetail) {
  vesselRouteData = (routeHistory || []).filter(p => p.lat && p.lon);
  const mapEmpty = document.getElementById('vesselMapEmpty');

  if (!vesselRouteData.length) {
    mapEmpty.style.display = 'block';
    document.getElementById('vesselMap').style.opacity = '0.3';
    return;
  }
  mapEmpty.style.display = 'none';

  if (vesselMapInstance) { vesselMapInstance.remove(); vesselMapInstance = null; }

  vesselMapInstance = L.map('vesselMap', { zoomControl: true, attributionControl: true });

  L.tileLayer('https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png', {
    attribution: '&copy; OpenStreetMap &copy; CARTO', maxZoom: 19,
  }).addTo(vesselMapInstance);

  // Detect actual data span and build adaptive toggles
  const now = Date.now() / 1000;
  const timestamps = vesselRouteData.map(p => p.ts || 0).filter(t => t > 0);
  const oldest = Math.min(...timestamps);
  const spanSec = now - oldest;
  const spanHours = Math.round(spanSec / 3600);

  function fmtSpan(sec) {
    const h = Math.round(sec / 3600);
    if (h < 1) return Math.round(sec / 60) + 'min';
    if (h < 48) return h + 'h';
    return Math.round(h / 24) + 'd';
  }

  // Build toggles: last ⅓, last ⅔, all — labeled with actual time spans
  const toggleContainer = document.querySelector('.map-range-toggles');
  const t1 = Math.round(spanSec / 3);
  const t2 = Math.round(spanSec * 2 / 3);
  toggleContainer.innerHTML =
    '<button class="map-range-btn" data-range="recent" onclick="setMapRange(&quot;recent&quot;)">Last ' + fmtSpan(t1) + '</button>' +
    '<button class="map-range-btn" data-range="half" onclick="setMapRange(&quot;half&quot;)">Last ' + fmtSpan(t2) + '</button>' +
    '<button class="map-range-btn active" data-range="all" onclick="setMapRange(&quot;all&quot;)">All (' + fmtSpan(spanSec) + ')</button>';

  // Show data cadence info
  if (timestamps.length >= 2) {
    const avgGap = spanSec / (timestamps.length - 1);
    const cadence = avgGap < 3600 ? Math.round(avgGap / 60) + 'min intervals' : fmtSpan(avgGap) + ' intervals';
    toggleContainer.innerHTML += '<span style="color:#484f58; font-size:11px; margin-left:12px; align-self:center;">' +
      timestamps.length + ' positions · ' + cadence + '</span>';
  }

  setMapRange('all');
}

function setMapRange(range) {
  currentMapRange = range;
  document.querySelectorAll('.map-range-btn').forEach(btn => {
    btn.classList.toggle('active', btn.getAttribute('data-range') === range);
  });

  if (!vesselMapInstance || !vesselRouteData.length) return;

  const now = Date.now() / 1000;
  const timestamps = vesselRouteData.map(p => p.ts || 0).filter(t => t > 0);
  const oldest = Math.min(...timestamps);
  const totalSpan = now - oldest;

  let points;
  if (range === 'all') {
    points = vesselRouteData;
  } else if (range === 'recent') {
    const cutoff = now - totalSpan / 3;
    points = vesselRouteData.filter(p => (p.ts || 0) >= cutoff);
  } else if (range === 'half') {
    const cutoff = now - (totalSpan * 2 / 3);
    points = vesselRouteData.filter(p => (p.ts || 0) >= cutoff);
  } else {
    // Fixed cutoffs as fallback
    const cutoffs = { '24h': 86400, '1w': 604800, '1m': 2592000 };
    const cutoff = now - (cutoffs[range] || totalSpan);
    points = vesselRouteData.filter(p => (p.ts || 0) >= cutoff);
  }
  if (!points.length) points = vesselRouteData;

  if (vesselRouteLayer) { vesselMapInstance.removeLayer(vesselRouteLayer); }
  if (vesselMarkers) { vesselMarkers.forEach(m => vesselMapInstance.removeLayer(m)); }
  vesselMarkers = [];

  const latLngs = points.map(p => [p.lat, p.lon]);
  vesselRouteLayer = L.polyline(latLngs, {
    color: '#58a6ff', weight: 3, opacity: 0.8, dashArray: '8 4',
  }).addTo(vesselMapInstance);

  points.forEach((p, i) => {
    const isLast = (i === points.length - 1);
    const marker = L.circleMarker([p.lat, p.lon], {
      radius: isLast ? 7 : 4,
      fillColor: isLast ? '#3fb950' : '#58a6ff',
      color: isLast ? '#fff' : '#30363d',
      weight: isLast ? 2 : 1, fillOpacity: 0.8,
    }).addTo(vesselMapInstance);
    const time = p.ts ? new Date(p.ts * 1000).toLocaleString() : '—';
    marker.bindPopup('<b>' + time + '</b><br>Lat: ' + p.lat.toFixed(4) + ', Lon: ' + p.lon.toFixed(4) + '<br>Speed: ' + (p.speed || 0) + ' kn');
    vesselMarkers.push(marker);
  });

  if (latLngs.length) {
    vesselMapInstance.fitBounds(L.latLngBounds(latLngs).pad(0.5), { maxZoom: 6 });
  }
}

// ── Sector ───────────────────────────────────────────────────────────────────
async function runSectorAnalysis(sector) {
  addProgress('Identifying key players in ' + sector + ' sector...', 'step');
  addProgress('Checking OFAC sanctions exposure...', 'step');

  const resp = await fetch('/api/sector-analysis', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ sector }),
  });
  if (!resp.ok) { addProgress('Error fetching sector data', 'error'); return; }

  const data = await resp.json();
  addProgress('Done!', 'done');
  document.getElementById('progressSpinner').style.display = 'none';

  renderSectorProfile(data);
}

function renderSectorProfile(data) {
  document.getElementById('sectorPanel').style.display = 'block';

  document.getElementById('sectorTitle').textContent = (data.sector_key || data.sector || '').toUpperCase() + ' SECTOR';
  document.getElementById('sectorSub').textContent = (data.company_count || 0) + ' key players tracked';
  document.getElementById('sectorCompanyCount').textContent = data.company_count || '—';
  document.getElementById('sectorSanctionCount').textContent = data.sanctioned_count || '0';
  const pct = data.company_count ? Math.round(data.sanctioned_count / data.company_count * 100) : 0;
  document.getElementById('sectorCoverage').textContent = pct + '%';

  const companiesEl = document.getElementById('sectorCompanies');
  companiesEl.innerHTML = (data.companies || []).map(co => {
    const sanctClass = co.is_sanctioned ? ' sanctioned' : '';
    const ticker = co.ticker ? '<div class="sc-ticker">' + co.ticker + '</div>' : '';
    const sanctLabel = co.is_sanctioned ? '<div style="font-size:10px;color:#f85149;margin-top:4px;">⚠ OFAC Listed</div>' : '';
    return '<div class="sector-company-card' + sanctClass + '">' +
      '<div class="sc-name">' + co.name + '</div>' +
      ticker +
      '<div class="sc-country">' + (co.country || '') + '</div>' +
      sanctLabel + '</div>';
  }).join('');

  // Graph
  const g = data.graph || {};
  sectorNetwork = renderVisGraph('sectorGraphContainer', 'sectorGraphEmpty', 'sectorGraphStats',
    g.nodes, g.edges, sectorNetwork);
}

function renderOrchestratorResults(data) {
  const panel = document.getElementById('orchestratorPanel');
  panel.style.display = 'block';

  document.getElementById('orchestratorSummary').textContent =
    data.executive_summary || 'No executive summary returned.';

  const findings = document.getElementById('orchestratorFindings');
  const findingRows = Array.isArray(data.findings) ? data.findings : [];
  findings.innerHTML = findingRows.length
    ? findingRows.slice(0, 12).map((f) => {
        const category = (f.category || 'General').toString();
        const text = (f.finding || '').toString();
        const conf = (f.confidence || 'LOW').toString();
        return '<li><div class="event-date">' + category + ' · ' + conf + '</div>' + text + '</li>';
      }).join('')
    : '<li style="color:#484f58">No findings returned</li>';

  const recs = document.getElementById('orchestratorRecommendations');
  const recRows = Array.isArray(data.recommendations) ? data.recommendations : [];
  recs.innerHTML = recRows.length
    ? recRows.slice(0, 12).map((r) => '<li>' + String(r) + '</li>').join('')
    : '<li style="color:#484f58">No recommendations returned</li>';
}

function addProgress(msg, type) {
  const log = document.getElementById('progressLog');
  const div = document.createElement('div');
  div.className = type;
  const time = new Date().toLocaleTimeString();
  div.textContent = '[' + time + '] ' + msg;
  log.appendChild(div);
  log.scrollTop = log.scrollHeight;
}

function renderResults(data) {
  document.getElementById('resultsPanel').style.display = 'block';

  const target = data.target;
  const sanctions = target.sanctions_status || {};
  const proj = data.projection.summary || {};
  const isSanctioned = sanctions.is_sanctioned;

  document.getElementById('impactInfoCards').innerHTML = `
    <div class="info-card">
      <h3>Target Company</h3>
      <div class="value">${target.name || target.ticker}</div>
      <div class="label">${target.ticker} &mdash; ${target.sector || 'N/A'} &mdash; ${target.country || 'N/A'}</div>
      <div class="sub-value">Current Price: <strong>$${(target.current_price || 0).toFixed(2)}</strong></div>
      ${target.market_cap ? '<div class="label">Market Cap: $' + (target.market_cap / 1e9).toFixed(1) + 'B</div>' : ''}
    </div>
    <div class="info-card">
      <h3>Sanctions Status</h3>
      <div style="margin-bottom: 8px;">
        <span class="sanctions-badge ${isSanctioned ? 'sanctioned' : 'clear'}">${isSanctioned ? 'Sanctioned' : 'Not Currently Sanctioned'}</span>
      </div>
      ${sanctions.lists && sanctions.lists.length ? '<div class="sub-value">Lists: ' + sanctions.lists.join(', ') + '</div>' : ''}
      ${sanctions.programs && sanctions.programs.length ? '<div class="label">Programs: ' + sanctions.programs.slice(0,3).join(', ') + '</div>' : ''}
      ${sanctions.csl_matches && sanctions.csl_matches.length ? '<div class="label">' + sanctions.csl_matches.length + ' Trade.gov CSL match(es)</div>' : ''}
    </div>
  `;

  // Projection summary
  const summaryCards = [];
  if (proj.pre_event_decline !== undefined) {
    const cls = proj.pre_event_decline < 0 ? 'negative' : 'positive';
    const sign = proj.pre_event_decline >= 0 ? '+' : '';
    summaryCards.push('<div class="proj-card"><div class="proj-label">Pre-Event Decline</div><div class="proj-value ' + cls + '">' + sign + proj.pre_event_decline.toFixed(1) + '%</div><div class="proj-note">Already priced in</div></div>');
  }
  ['day_30', 'day_60', 'day_90'].forEach(key => {
    const val = proj[key + '_post'];
    const range = proj[key + '_range'];
    const label = key.replace('day_', '') + '-Day Post';
    if (val === undefined) { summaryCards.push('<div class="proj-card"><div class="proj-label">' + label + '</div><div class="proj-value" style="color:#8b949e">N/A</div></div>'); return; }
    const cls = val < 0 ? 'negative' : 'positive';
    const sign = val >= 0 ? '+' : '';
    summaryCards.push('<div class="proj-card"><div class="proj-label">' + label + '</div><div class="proj-value ' + cls + '">' + sign + val.toFixed(1) + '%</div>' + (range ? '<div class="proj-range">' + range[0].toFixed(1) + '% to ' + range[1].toFixed(1) + '%</div>' : '') + '</div>');
  });
  if (proj.max_drawdown !== undefined) {
    const cls = proj.max_drawdown < 0 ? 'negative' : 'positive';
    const sign = proj.max_drawdown >= 0 ? '+' : '';
    summaryCards.push('<div class="proj-card"><div class="proj-label">Peak-to-Trough</div><div class="proj-value ' + cls + '">' + sign + proj.max_drawdown.toFixed(1) + '%</div><div class="proj-note">Worst point, full window</div></div>');
  }
  document.getElementById('projectionSummary').innerHTML = summaryCards.join('');

  // Comparables table with toggle
  const tbody = document.querySelector('#comparablesTable tbody');
  tbody.innerHTML = data.comparables.map((c, i) =>
    '<tr data-idx="' + i + '" onclick="toggleComparable(' + i + ')">' +
    '<td><span class="color-dot" style="background:' + c.color + '"></span></td>' +
    '<td>' + c.name + '</td>' +
    '<td style="font-family:monospace;color:#58a6ff;">' + c.ticker + '</td>' +
    '<td>' + c.sanction_date + '</td>' +
    '<td style="color:#8b949e;font-size:12px;">' + c.description + '</td></tr>'
  ).join('');

  renderChart(data);
}

function toggleComparable(idx) {
  if (!impactChart) return;
  const meta = impactChart.getDatasetMeta(idx);
  meta.hidden = !meta.hidden;
  impactChart.update();

  // Toggle dimmed class on table row
  const row = document.querySelector('#comparablesTable tbody tr[data-idx="' + idx + '"]');
  if (row) row.classList.toggle('dimmed', meta.hidden);
}

function renderChart(data) {
  const ctx = document.getElementById('impactChart').getContext('2d');
  const datasets = [];

  // Comparable curves — translucent so projection line stands out
  data.comparables.forEach(comp => {
    // Convert hex color to rgba with 0.35 opacity
    const hex = comp.color;
    const r = parseInt(hex.slice(1,3), 16);
    const g = parseInt(hex.slice(3,5), 16);
    const b = parseInt(hex.slice(5,7), 16);
    datasets.push({
      label: comp.name + ' (' + comp.sanction_date.slice(0,4) + ')',
      data: comp.curve.map(p => ({ x: p.day, y: p.pct })),
      borderColor: 'rgba(' + r + ',' + g + ',' + b + ', 0.35)',
      hoverBorderColor: hex,
      borderWidth: 1.2,
      pointRadius: 0,
      pointHoverRadius: 4,
      pointHoverBorderWidth: 2,
      tension: 0.2,
      fill: false,
    });
  });

  // Confidence band
  if (data.projection.upper && data.projection.upper.length > 0) {
    datasets.push({
      label: 'Confidence Band (1\\u03c3)',
      data: data.projection.upper.map(p => ({ x: p.day, y: p.pct })),
      borderColor: 'transparent',
      backgroundColor: 'rgba(88, 166, 255, 0.12)',
      pointRadius: 0,
      fill: '+1',
      order: 10,
    });
    datasets.push({
      label: '_lower',
      data: data.projection.lower.map(p => ({ x: p.day, y: p.pct })),
      borderColor: 'transparent',
      backgroundColor: 'transparent',
      pointRadius: 0,
      fill: false,
      order: 10,
    });
  }

  // Projection mean — bold white line, stands out from translucent comparables
  if (data.projection.mean && data.projection.mean.length > 0) {
    datasets.push({
      label: 'Projected Impact (' + data.target.ticker + ')',
      data: data.projection.mean.map(p => ({ x: p.day, y: p.pct })),
      borderColor: '#ffffff',
      borderWidth: 4,
      borderDash: [10, 5],
      pointRadius: 0,
      pointHoverRadius: 6,
      pointHoverBorderWidth: 3,
      tension: 0.2,
      fill: false,
      order: 0,
    });
  }

  impactChart = new Chart(ctx, {
    type: 'line',
    data: { datasets },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      interaction: { mode: 'nearest', intersect: true, axis: 'x' },
      scales: {
        x: {
          type: 'linear',
          title: { display: true, text: 'Trading Days from Sanctions Event', color: '#8b949e', font: { size: 12 } },
          grid: { color: '#21262d' },
          ticks: { color: '#8b949e', font: { size: 11 } },
        },
        y: {
          title: { display: true, text: 'Price Change (%)', color: '#8b949e', font: { size: 12 } },
          grid: { color: '#21262d' },
          ticks: { color: '#8b949e', font: { size: 11 }, callback: v => v + '%' },
        },
      },
      plugins: {
        legend: {
          position: 'top',
          labels: {
            color: '#c9d1d9', font: { size: 11 },
            usePointStyle: true, pointStyle: 'line',
            filter: item => !item.text.startsWith('_'),
            padding: 16,
          },
        },
        tooltip: {
          backgroundColor: '#161b22',
          borderColor: '#30363d', borderWidth: 1,
          titleColor: '#e6edf3', bodyColor: '#c9d1d9',
          displayColors: true,
          filter: item => !item.dataset.label.startsWith('_'),
          callbacks: {
            title: items => items[0].dataset.label,
            label: item => {
              if (item.dataset.label.startsWith('_')) return null;
              return 'Day ' + item.parsed.x + ':  ' + (item.parsed.y >= 0 ? '+' : '') + item.parsed.y.toFixed(1) + '%';
            },
          },
        },
        annotation: {
          annotations: {
            sanctionLine: {
              type: 'line', xMin: 0, xMax: 0,
              borderColor: '#f85149', borderWidth: 2, borderDash: [6, 3],
              label: {
                display: true, content: 'SANCTIONS EVENT', position: 'start',
                backgroundColor: 'rgba(248, 81, 73, 0.15)', color: '#f85149',
                font: { size: 10, weight: 'bold' },
                padding: { top: 4, bottom: 4, left: 8, right: 8 },
              },
            },
            zeroLine: {
              type: 'line', yMin: 0, yMax: 0,
              borderColor: '#30363d', borderWidth: 1,
            },
          },
        },
      },
    },
  });
}

document.getElementById('queryInput').addEventListener('keydown', (e) => {
  if (e.key === 'Enter') { e.preventDefault(); startAnalysis(); }
});
</script>
</body>
</html>"""
