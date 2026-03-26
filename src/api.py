"""FastAPI web server for the Economic Warfare OSINT system.

Run with:
    uv run uvicorn src.api:app --reload --host 0.0.0.0 --port 8000
"""

from __future__ import annotations

import asyncio
import re
import uuid
import webbrowser
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel

from src.common.config import config
from src.fusion.renderer import render_entity_graph
from src.orchestrator.main import Orchestrator
from src.orchestrator.tool_registry import ToolRegistry
from src.sanctions_impact import run_sanctions_impact
from src.tools.corporate.server import get_beneficial_owners, get_corporate_tree
from src.tools.geopolitical.client import refresh_acled_token

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

_browser_opened = False


@app.on_event("startup")
async def _startup() -> None:
    global _browser_opened
    await refresh_acled_token()
    if not _browser_opened:
        _browser_opened = True
        webbrowser.open("http://localhost:8000")


# --- In-memory state (commented out — orchestrator disabled for demo) ---
_analyses: dict[str, dict[str, Any]] = {}


# --- Request / Response models ---

class SanctionsImpactRequest(BaseModel):
    ticker: str


class EntityGraphRequest(BaseModel):
    query: str


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
    _analyses[analysis_id]["progress"].append("Starting orchestrator analysis")
    try:
        orchestrator = Orchestrator()
        assessment = await orchestrator.analyze(query)
        _analyses[analysis_id]["result"] = assessment.model_dump(mode="json")
        _analyses[analysis_id]["status"] = "completed"
        _analyses[analysis_id]["progress"].append("Analysis complete")
    except Exception as e:
        _analyses[analysis_id]["status"] = "failed"
        _analyses[analysis_id]["error"] = str(e)
        _analyses[analysis_id]["progress"].append(f"Error: {e}")


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
        return JSONResponse(content=result)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# --- Entity Graph endpoint ---

_ENTITY_COLORS: dict[str, str] = {
    "company": "#4A90D9", "person": "#7B68EE", "government": "#DC143C",
    "vessel": "#2E8B57", "sanctions_list": "#F85149",
    "theme": "#F0883E", "sector": "#3FB950",
}


def _truncate(s: str, n: int = 28) -> str:
    return s if len(s) <= n else s[:n - 1] + "…"


def _node(nid: str, name: str, entity_type: str, country: str | None = None) -> dict[str, Any]:
    title = f"{name}\n{entity_type}" + (f" · {country}" if country else "")
    return {"id": nid, "label": _truncate(name), "title": title,
            "group": entity_type, "color": _ENTITY_COLORS.get(entity_type, "#808080")}


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


def _build_graph_data(tree: dict, owners: dict, query: str) -> tuple[list, list]:
    """Extract vis.js nodes and edges from CorporateTree and BeneficialOwnerResult dicts."""
    nodes: dict[str, dict] = {}
    edges: dict[str, dict] = {}  # keyed by "from→to→label" to deduplicate

    def add_node(nid: str, name: str, etype: str, country: str | None = None) -> None:
        if nid and name and nid not in nodes:
            nodes[nid] = _node(nid, name, etype, country)

    def add_edge(src: str, tgt: str, label: str, dashes: bool = False) -> None:
        if src in nodes and tgt in nodes and src != tgt:
            key = f"{src}→{tgt}→{label}"
            if key not in edges:
                edges[key] = {"from": src, "to": tgt,
                              "label": label.replace("_", " "), "arrows": "to", "dashes": dashes}

    def slug(s: str) -> str:
        return s.lower().replace(" ", "_").replace(",", "").replace(".", "")[:64]

    # ── Corporate tree ────────────────────────────────────────────────────────
    tree_data = tree.get("data", {}) if isinstance(tree, dict) else {}
    if tree_data:
        entity_name = tree_data.get("entity_name") or query
        main_id = slug(entity_name)
        add_node(main_id, entity_name, "company")

        # LEI records → company nodes; build lei→id map for ownership links
        lei_map: dict[str, str] = {}
        for rec in (tree_data.get("lei_records") or []):
            lei_raw = (rec.get("lei") or "").strip()
            name = (rec.get("legal_name") or "").strip()
            c_lei = _canonical_lei(lei_raw)
            nid = c_lei or (lei_raw if lei_raw else "")
            if not nid and not name:
                continue
            disp = name or (f"LEI {nid}" if nid else "Entity")
            if not nid:
                nid = slug(disp)
            for alias in {a for a in (c_lei, lei_raw, nid) if a}:
                lei_map[alias] = nid
            add_node(nid, disp, "company", rec.get("country"))

        def _ensure_lei_endpoint(raw_ref: str) -> str | None:
            """Add a minimal company node for an LEI referenced only on a relationship."""
            raw = str(raw_ref or "").strip()
            if not raw:
                return None
            hit = _lei_resolve_node_id(lei_map, raw)
            if hit is not None:
                return hit
            c = _canonical_lei(raw)
            nid = c or raw[:64]
            for alias in {a for a in (raw, c, nid) if a}:
                lei_map[alias] = nid
            add_node(nid, f"LEI {c}" if c else _truncate(raw, 40), "company")
            return nid

        # Ownership links → edges (GLEIF may use bare LEIs or JSON:API href ids)
        for link in (tree_data.get("ownership_links") or []):
            raw_p = str(link.get("parent_id", "") or "")
            raw_c = str(link.get("child_id", "") or "")
            pid = _lei_resolve_node_id(lei_map, raw_p)
            cid = _lei_resolve_node_id(lei_map, raw_c)
            if pid is None and raw_p:
                pid = _ensure_lei_endpoint(raw_p)
            if cid is None and raw_c:
                cid = _ensure_lei_endpoint(raw_c)
            rel = link.get("relationship_type", "subsidiary_of")
            if pid and cid:
                add_edge(cid, pid, rel)
            elif pid and main_id:
                # child is the queried entity itself (lookup missed child LEI)
                add_edge(main_id, pid, rel)

        # OpenCorporates companies with officers
        for comp in (tree_data.get("companies") or []):
            cname = comp.get("name", "")
            if not cname:
                continue
            cid = slug(cname)
            add_node(cid, cname, "company", comp.get("jurisdiction"))
            add_edge(cid, main_id, "search result", dashes=True)

            for officer in (comp.get("officers") or []):
                oname = officer.get("name", "")
                if not oname:
                    continue
                oid = slug(oname)
                add_node(oid, oname, "person")
                add_edge(oid, cid, officer.get("role", "officer"))

    # ── Beneficial owners ────────────────────────────────────────────────────
    owners_data = owners.get("data", {}) if isinstance(owners, dict) else {}
    if owners_data:
        entity_name = owners_data.get("entity_name") or query
        main_id = slug(entity_name)
        add_node(main_id, entity_name, "company")

        for officer in (owners_data.get("officers") or []):
            oname = officer.get("name", "")
            if not oname:
                continue
            oid = slug(oname)
            add_node(oid, oname, "person")
            add_edge(oid, main_id, officer.get("role", "officer"))

        for entity in (owners_data.get("offshore_connections") or []):
            ename = entity.get("name", "")
            if not ename:
                continue
            eid = slug(ename)
            add_node(eid, ename, "company", entity.get("jurisdiction"))
            add_edge(main_id, eid, "offshore connection")

    # When GLEIF returns many hits but no ownership/OC relationships, avoid N isolates.
    tree_tail = tree.get("data", {}) if isinstance(tree, dict) else {}
    if tree_tail and not edges:
        root = slug(tree_tail.get("entity_name") or query)
        for rec in (tree_tail.get("lei_records") or [])[:15]:
            lei_raw = (rec.get("lei") or "").strip()
            name = (rec.get("legal_name") or "").strip()
            hit_id = _canonical_lei(lei_raw) or (slug(name) if name else "")
            if hit_id and root in nodes and hit_id in nodes and hit_id != root:
                add_edge(root, hit_id, "GLEIF match", dashes=True)

    return list(nodes.values()), list(edges.values())


@app.post("/api/entity-graph")
async def entity_graph_endpoint(req: EntityGraphRequest):
    """Build vis.js entity graph from corporate tool data."""
    query = req.query.strip()
    if not query:
        raise HTTPException(status_code=400, detail="Query cannot be empty")
    try:
        tree, owners = await asyncio.wait_for(
            asyncio.gather(
                get_corporate_tree(query),
                get_beneficial_owners(query),
                return_exceptions=True,
            ),
            timeout=20.0,
        )
        graph_nodes, graph_edges = _build_graph_data(
            tree if not isinstance(tree, BaseException) else {},
            owners if not isinstance(owners, BaseException) else {},
            query,
        )
        return JSONResponse(content={
            "nodes": graph_nodes,
            "edges": graph_edges,
            "meta": {"query": query, "node_count": len(graph_nodes), "edge_count": len(graph_edges)},
        })
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# --- Background analysis runner (commented out for demo) ---
# async def _run_analysis(analysis_id: str, query: str) -> None:
#     """Run the full analysis pipeline, updating status as we go."""
#     ... (preserved in git history)


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
<script src="https://cdn.jsdelivr.net/npm/chartjs-plugin-annotation"></script>
<script src="https://cdn.jsdelivr.net/npm/vis-network@9.1.9/standalone/umd/vis-network.min.js"></script>
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/vis-network@9.1.9/styles/vis-network.min.css">
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
  .proj-card .proj-value { font-size: 22px; font-weight: 600; margin-top: 4px; }
  .proj-card .proj-range { font-size: 12px; color: #8b949e; margin-top: 4px; }
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
</style>
</head>
<body>

<div class="header">
  <h1>Economic Warfare OSINT — Sanctions Impact Projector</h1>
  <p>Analyze how sanctions affect publicly traded stocks using historical comparable data</p>
</div>

<div class="main">

  <div class="query-box">
    <input type="text" id="queryInput" placeholder="What happens if we sanction...? (enter a company name or stock ticker)" />
    <div class="btn-row">
      <button class="btn btn-primary" id="analyzeBtn" onclick="startAnalysis()">Analyze Impact</button>
      <button class="btn btn-secondary" onclick="clearAll()">Clear</button>
      <span id="healthBadge"></span>
    </div>
    <div class="examples">
      <span class="example-chip" data-ticker="BABA" onclick="runExample(this)">Sanction Alibaba (BABA)</span>
      <span class="example-chip" data-ticker="0981.HK" onclick="runExample(this)">Sanction SMIC (0981.HK)</span>
      <span class="example-chip" data-ticker="TSM" onclick="runExample(this)">What if we sanction TSMC? (TSM)</span>
      <span class="example-chip" data-ticker="BIDU" onclick="runExample(this)">Sanction Baidu (BIDU)</span>
      <span class="example-chip" data-ticker="0763.HK" onclick="runExample(this)">ZTE Corp (0763.HK)</span>
      <span class="example-chip" data-ticker="INTC" onclick="runExample(this)">Intel chip restrictions (INTC)</span>
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

  <!-- Entity Relationship Graph — commented out, returning later
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
  -->

</div>

<script>
let impactChart = null;
let lastData = null;
// let visNetwork = null;

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
  document.getElementById('queryInput').value = el.textContent;
  startAnalysis(el.dataset.ticker);
}

function clearAll() {
  document.getElementById('resultsPanel').style.display = 'none';
  document.getElementById('progressPanel').classList.remove('active');
  document.getElementById('progressLog').innerHTML = '';
  document.getElementById('queryInput').value = '';
  if (impactChart) { impactChart.destroy(); impactChart = null; }
  lastData = null;
  // document.getElementById('graphSection').classList.remove('active');
  // document.getElementById('graphEmpty').style.display = 'block';
  // document.getElementById('graphEmpty').textContent = 'Loading entity graph...';
  // document.getElementById('graphStats').textContent = '';
  // if (visNetwork) { visNetwork.destroy(); visNetwork = null; }
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

  const ticker = tickerOverride || extractTicker(raw);
  if (!ticker) return;

  const btn = document.getElementById('analyzeBtn');
  btn.disabled = true;
  document.getElementById('resultsPanel').style.display = 'none';
  if (impactChart) { impactChart.destroy(); impactChart = null; }

  const panel = document.getElementById('progressPanel');
  panel.classList.add('active');
  document.getElementById('progressLog').innerHTML = '';
  document.getElementById('progressSpinner').style.display = 'inline-block';

  addProgress('Resolving ticker: ' + ticker, 'step');
  addProgress('Checking sanctions status (OFAC, OpenSanctions, Trade.gov CSL)...', 'step');
  addProgress('Fetching historical comparable data...', 'step');

  try {
    const resp = await fetch('/api/sanctions-impact', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ ticker }),
    });

    if (!resp.ok) {
      const err = await resp.json();
      addProgress('Error: ' + (err.detail || 'Request failed'), 'error');
      btn.disabled = false;
      document.getElementById('progressSpinner').style.display = 'none';
      return;
    }

    const data = await resp.json();
    lastData = data;
    addProgress('Found ' + data.metadata.comparable_count + ' comparable sanctions cases', 'step');
    addProgress('Computing projection with confidence interval...', 'step');
    addProgress('Done!', 'done');
    document.getElementById('progressSpinner').style.display = 'none';

    renderResults(data);
    // loadEntityGraph(ticker);  // fire-and-forget, appears below the fold
  } catch (e) {
    addProgress('Error: ' + e.message, 'error');
    document.getElementById('progressSpinner').style.display = 'none';
  }
  btn.disabled = false;
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
  document.getElementById('projectionSummary').innerHTML = ['day_30', 'day_60', 'day_90'].map(key => {
    const expected = proj[key + '_expected'];
    const range = proj[key + '_range'];
    const label = key.replace('day_', '') + '-Day';
    if (expected === undefined) return '<div class="proj-card"><div class="proj-label">' + label + '</div><div class="proj-value" style="color:#8b949e">N/A</div></div>';
    const cls = expected < 0 ? 'negative' : 'positive';
    const sign = expected >= 0 ? '+' : '';
    return '<div class="proj-card"><div class="proj-label">' + label + ' Projection</div><div class="proj-value ' + cls + '">' + sign + expected.toFixed(1) + '%</div>' + (range ? '<div class="proj-range">' + range[0].toFixed(1) + '% to ' + range[1].toFixed(1) + '%</div>' : '') + '</div>';
  }).join('');

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
