"""FastAPI web server for the Economic Warfare OSINT system.

Run with:
    uv run uvicorn src.api:app --reload --host 0.0.0.0 --port 8000
"""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from src.common.config import config
from src.fusion.graph_builder import build_graph_from_assessment, build_graph_from_results
from src.fusion.renderer import render_graph_data, render_json, render_markdown
from src.orchestrator.main import Orchestrator, _extract_json
from src.orchestrator.tool_registry import ToolRegistry

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

# --- In-memory state for active analyses ---
_analyses: dict[str, dict[str, Any]] = {}


# --- Request / Response models ---

class AnalyzeRequest(BaseModel):
    query: str


class AnalyzeResponse(BaseModel):
    analysis_id: str
    status: str


class AnalysisStatus(BaseModel):
    analysis_id: str
    status: str  # "pending" | "decomposing" | "executing" | "synthesizing" | "complete" | "error"
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


# --- Analysis endpoints ---

@app.post("/api/analyze", response_model=AnalyzeResponse)
async def start_analysis(req: AnalyzeRequest):
    """Start an async analysis. Returns an analysis_id to poll for status."""
    if not req.query.strip():
        raise HTTPException(status_code=400, detail="Query cannot be empty")

    issues = config.validate()
    if issues:
        raise HTTPException(status_code=503, detail=f"Config issues: {', '.join(issues)}")

    analysis_id = str(uuid.uuid4())[:8]
    _analyses[analysis_id] = {
        "status": "pending",
        "progress": [],
        "query": req.query,
        "result": None,
        "error": None,
        "started_at": datetime.now(timezone.utc).isoformat(),
    }

    # Fire off analysis in background
    asyncio.create_task(_run_analysis(analysis_id, req.query))

    return AnalyzeResponse(analysis_id=analysis_id, status="pending")


@app.get("/api/analyze/{analysis_id}", response_model=AnalysisStatus)
async def get_analysis(analysis_id: str):
    """Poll for analysis status and results."""
    if analysis_id not in _analyses:
        raise HTTPException(status_code=404, detail="Analysis not found")

    state = _analyses[analysis_id]
    result_data = None
    markdown = None
    graph_data = None

    if state["result"] is not None:
        assessment = state["result"]
        result_data = json.loads(render_json(assessment))
        markdown = render_markdown(assessment)
        graph_data = render_graph_data(assessment)

    return AnalysisStatus(
        analysis_id=analysis_id,
        status=state["status"],
        progress=state["progress"],
        result=result_data,
        markdown=markdown,
        graph_data=graph_data,
        error=state.get("error"),
    )


# --- WebSocket for live progress ---

@app.websocket("/ws/analyze/{analysis_id}")
async def ws_analysis(websocket: WebSocket, analysis_id: str):
    """WebSocket endpoint for live progress updates during analysis."""
    await websocket.accept()

    if analysis_id not in _analyses:
        await websocket.send_json({"error": "Analysis not found"})
        await websocket.close()
        return

    last_progress_len = 0
    try:
        while True:
            state = _analyses.get(analysis_id, {})
            status = state.get("status", "unknown")

            # Send new progress messages
            progress = state.get("progress", [])
            if len(progress) > last_progress_len:
                for msg in progress[last_progress_len:]:
                    await websocket.send_json({"type": "progress", "message": msg})
                last_progress_len = len(progress)

            if status in ("complete", "error"):
                if status == "complete" and state.get("result"):
                    assessment = state["result"]
                    await websocket.send_json({
                        "type": "complete",
                        "result": json.loads(render_json(assessment)),
                        "markdown": render_markdown(assessment),
                        "graph_data": render_graph_data(assessment),
                    })
                elif status == "error":
                    await websocket.send_json({
                        "type": "error",
                        "error": state.get("error", "Unknown error"),
                    })
                break

            await asyncio.sleep(0.5)
    except WebSocketDisconnect:
        pass
    finally:
        await websocket.close()


# --- Synchronous single-shot endpoint (simpler, blocks until done) ---

@app.post("/api/analyze/sync")
async def analyze_sync(req: AnalyzeRequest):
    """Run analysis synchronously and return full result. Simpler but blocks."""
    if not req.query.strip():
        raise HTTPException(status_code=400, detail="Query cannot be empty")

    issues = config.validate()
    if issues:
        raise HTTPException(status_code=503, detail=f"Config issues: {', '.join(issues)}")

    try:
        orchestrator = Orchestrator()
        assessment = await orchestrator.analyze(req.query)
        return {
            "status": "complete",
            "result": json.loads(render_json(assessment)),
            "markdown": render_markdown(assessment),
            "graph_data": render_graph_data(assessment),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# --- Background analysis runner ---

async def _run_analysis(analysis_id: str, query: str) -> None:
    """Run the full analysis pipeline, updating status as we go."""
    state = _analyses[analysis_id]

    try:
        state["status"] = "decomposing"
        state["progress"].append("Decomposing question into research plan...")

        orchestrator = Orchestrator()

        # Step 1: Decompose
        plan = await orchestrator._decompose(query)
        state["progress"].append(f"Research plan: {len(plan)} steps")

        # Step 2: Execute
        state["status"] = "executing"
        state["progress"].append("Executing research plan...")

        # Execute with progress tracking
        results: dict[str, Any] = {}
        completed_steps: set[int] = set()

        while len(completed_steps) < len(plan):
            ready = []
            for step in plan:
                step_num = step.get("step", 0)
                if step_num in completed_steps:
                    continue
                deps = step.get("depends_on", [])
                if all(d in completed_steps for d in deps):
                    ready.append(step)

            if not ready:
                break

            tasks = [orchestrator._execute_step(step, results) for step in ready]
            step_results = await asyncio.gather(*tasks, return_exceptions=True)

            for step, result in zip(ready, step_results):
                step_num = step.get("step", 0)
                completed_steps.add(step_num)
                desc = step.get("description", f"Step {step_num}")
                if isinstance(result, Exception):
                    results[f"step_{step_num}"] = {"error": str(result), "description": desc}
                    state["progress"].append(f"Step {step_num} failed: {desc}")
                else:
                    results[f"step_{step_num}"] = result
                    state["progress"].append(f"Step {step_num} complete: {desc}")

        # Step 3: Synthesize
        state["status"] = "synthesizing"
        state["progress"].append("Synthesizing findings...")

        assessment = await orchestrator._synthesize(query, results)

        # Build graph from tool results + assessment findings
        graph = build_graph_from_results(results)
        assessment.entity_graph.merge(graph)
        assessment_graph = build_graph_from_assessment(assessment)
        assessment.entity_graph.merge(assessment_graph)

        state["result"] = assessment
        state["status"] = "complete"
        state["progress"].append("Analysis complete.")

    except Exception as e:
        state["status"] = "error"
        state["error"] = str(e)
        state["progress"].append(f"Error: {e}")


# --- Inline frontend ---

def _read_index_html() -> str:
    """Return the embedded single-page frontend."""
    return """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Economic Warfare OSINT</title>
<script src="https://unpkg.com/vis-network/standalone/umd/vis-network.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/marked/marked.min.js"></script>
<style>
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body { font-family: 'Segoe UI', system-ui, -apple-system, sans-serif; background: #0a0e17; color: #c9d1d9; min-height: 100vh; }

  .header { background: linear-gradient(135deg, #0d1117 0%, #161b22 100%); border-bottom: 1px solid #30363d; padding: 20px 32px; }
  .header h1 { font-size: 24px; color: #e6edf3; font-weight: 600; }
  .header p { color: #8b949e; font-size: 14px; margin-top: 4px; }

  .main { max-width: 1400px; margin: 0 auto; padding: 24px 32px; }

  .query-box { background: #161b22; border: 1px solid #30363d; border-radius: 8px; padding: 20px; margin-bottom: 24px; }
  .query-box textarea { width: 100%; background: #0d1117; border: 1px solid #30363d; border-radius: 6px; color: #e6edf3; padding: 12px; font-size: 15px; resize: vertical; min-height: 60px; font-family: inherit; }
  .query-box textarea:focus { outline: none; border-color: #58a6ff; box-shadow: 0 0 0 3px rgba(88,166,255,0.15); }
  .query-box textarea::placeholder { color: #484f58; }

  .btn-row { display: flex; gap: 12px; margin-top: 12px; align-items: center; }
  .btn { padding: 8px 20px; border-radius: 6px; border: 1px solid #30363d; cursor: pointer; font-size: 14px; font-weight: 500; transition: all 0.15s; }
  .btn-primary { background: #238636; border-color: #238636; color: #fff; }
  .btn-primary:hover { background: #2ea043; }
  .btn-primary:disabled { opacity: 0.5; cursor: not-allowed; }
  .btn-secondary { background: #21262d; color: #c9d1d9; }
  .btn-secondary:hover { background: #30363d; }

  .examples { display: flex; gap: 8px; flex-wrap: wrap; margin-top: 12px; }
  .example-chip { background: #1c2129; border: 1px solid #30363d; border-radius: 16px; padding: 4px 12px; font-size: 12px; color: #8b949e; cursor: pointer; transition: all 0.15s; }
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

  .results { display: none; }
  .results.active { display: block; }

  .tabs { display: flex; gap: 0; border-bottom: 1px solid #30363d; margin-bottom: 16px; }
  .tab { padding: 8px 16px; font-size: 14px; color: #8b949e; cursor: pointer; border-bottom: 2px solid transparent; transition: all 0.15s; }
  .tab:hover { color: #e6edf3; }
  .tab.active { color: #58a6ff; border-bottom-color: #58a6ff; }

  .tab-content { display: none; }
  .tab-content.active { display: block; }

  .report-panel { background: #161b22; border: 1px solid #30363d; border-radius: 8px; padding: 24px; line-height: 1.7; }
  .report-panel h1 { font-size: 22px; color: #e6edf3; margin-bottom: 16px; }
  .report-panel h2 { font-size: 18px; color: #e6edf3; margin: 20px 0 8px; border-bottom: 1px solid #21262d; padding-bottom: 4px; }
  .report-panel h3 { font-size: 15px; color: #c9d1d9; margin: 12px 0 4px; }
  .report-panel p { margin: 8px 0; }
  .report-panel table { width: 100%; border-collapse: collapse; margin: 8px 0; }
  .report-panel th, .report-panel td { padding: 8px 12px; border: 1px solid #30363d; text-align: left; font-size: 13px; }
  .report-panel th { background: #21262d; color: #e6edf3; }
  .report-panel pre { background: #0d1117; border: 1px solid #30363d; border-radius: 6px; padding: 12px; overflow-x: auto; font-size: 12px; }
  .report-panel code { font-family: 'Cascadia Code', 'Fira Code', monospace; }
  .report-panel ul, .report-panel ol { padding-left: 24px; }

  #graph-container { width: 100%; height: 500px; background: #0d1117; border: 1px solid #30363d; border-radius: 8px; }

  .json-panel { background: #0d1117; border: 1px solid #30363d; border-radius: 8px; padding: 16px; max-height: 600px; overflow: auto; }
  .json-panel pre { font-family: 'Cascadia Code', 'Fira Code', monospace; font-size: 12px; color: #c9d1d9; white-space: pre-wrap; }

  .status-badge { display: inline-block; padding: 2px 8px; border-radius: 12px; font-size: 12px; font-weight: 500; }
  .status-badge.ok { background: #238636; color: #fff; }
  .status-badge.error { background: #da3633; color: #fff; }
</style>
</head>
<body>

<div class="header">
  <h1>Economic Warfare OSINT System</h1>
  <p>Multi-agent scenario analysis &mdash; sanctions, supply chains, investment interception</p>
</div>

<div class="main">
  <div class="query-box">
    <textarea id="query" placeholder="Ask a question... e.g., What happens if we sanction Fujian Jinhua?" rows="2"></textarea>
    <div class="btn-row">
      <button class="btn btn-primary" id="analyzeBtn" onclick="startAnalysis()">Analyze</button>
      <button class="btn btn-secondary" onclick="clearResults()">Clear</button>
      <span id="healthBadge"></span>
    </div>
    <div class="examples">
      <span class="example-chip" onclick="setQuery(this)">What happens if we sanction Fujian Jinhua?</span>
      <span class="example-chip" onclick="setQuery(this)">What is the supply chain impact of sanctioning Norinco's subsidiary in Malaysia?</span>
      <span class="example-chip" onclick="setQuery(this)">China is investing $30M in a port in Sri Lanka — how do we intersect?</span>
      <span class="example-chip" onclick="setQuery(this)">What are the downstream effects of sanctioning Russian oil exports?</span>
    </div>
  </div>

  <div class="progress-panel" id="progressPanel">
    <h3><span class="spinner" id="progressSpinner"></span>Analysis Progress</h3>
    <div class="progress-log" id="progressLog"></div>
  </div>

  <div class="results" id="results">
    <div class="tabs">
      <div class="tab active" data-tab="report" onclick="switchTab(this)">Report</div>
      <div class="tab" data-tab="graph" onclick="switchTab(this)">Entity Graph</div>
      <div class="tab" data-tab="json" onclick="switchTab(this)">Raw JSON</div>
    </div>

    <div class="tab-content active" id="tab-report">
      <div class="report-panel" id="reportContent"></div>
    </div>

    <div class="tab-content" id="tab-graph">
      <div id="graph-container"></div>
    </div>

    <div class="tab-content" id="tab-json">
      <div class="json-panel"><pre id="jsonContent"></pre></div>
    </div>
  </div>
</div>

<script>
let currentAnalysisId = null;
let ws = null;

// Check health on load
fetch('/api/health').then(r => r.json()).then(data => {
  const badge = document.getElementById('healthBadge');
  if (data.status === 'ok') {
    badge.innerHTML = '<span class="status-badge ok">API Connected</span>';
  } else {
    badge.innerHTML = '<span class="status-badge error">' + data.issues.join(', ') + '</span>';
  }
});

function setQuery(el) {
  document.getElementById('query').value = el.textContent;
}

function clearResults() {
  document.getElementById('results').classList.remove('active');
  document.getElementById('progressPanel').classList.remove('active');
  document.getElementById('progressLog').innerHTML = '';
  document.getElementById('query').value = '';
}

async function startAnalysis() {
  const query = document.getElementById('query').value.trim();
  if (!query) return;

  const btn = document.getElementById('analyzeBtn');
  btn.disabled = true;

  // Show progress
  const panel = document.getElementById('progressPanel');
  panel.classList.add('active');
  document.getElementById('progressLog').innerHTML = '';
  document.getElementById('results').classList.remove('active');
  document.getElementById('progressSpinner').style.display = 'inline-block';

  addProgress('Submitting query...', 'step');

  try {
    const resp = await fetch('/api/analyze', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ query }),
    });
    const data = await resp.json();

    if (!resp.ok) {
      addProgress(data.detail || 'Failed to start analysis', 'error');
      btn.disabled = false;
      return;
    }

    currentAnalysisId = data.analysis_id;
    addProgress('Analysis started (ID: ' + data.analysis_id + ')', 'step');

    // Connect WebSocket for live updates
    connectWS(data.analysis_id);
  } catch (e) {
    addProgress('Connection error: ' + e.message, 'error');
    btn.disabled = false;
  }
}

function connectWS(analysisId) {
  const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
  ws = new WebSocket(proto + '//' + location.host + '/ws/analyze/' + analysisId);

  ws.onmessage = (event) => {
    const msg = JSON.parse(event.data);

    if (msg.type === 'progress') {
      addProgress(msg.message, 'step');
    } else if (msg.type === 'complete') {
      addProgress('Analysis complete!', 'done');
      document.getElementById('progressSpinner').style.display = 'none';
      document.getElementById('analyzeBtn').disabled = false;
      showResults(msg);
    } else if (msg.type === 'error') {
      addProgress('Error: ' + msg.error, 'error');
      document.getElementById('progressSpinner').style.display = 'none';
      document.getElementById('analyzeBtn').disabled = false;
    }
  };

  ws.onerror = () => {
    // Fallback to polling if WS fails
    pollAnalysis(analysisId);
  };

  ws.onclose = () => {
    // If not complete yet, fall back to polling
    const btn = document.getElementById('analyzeBtn');
    if (btn.disabled) {
      pollAnalysis(analysisId);
    }
  };
}

async function pollAnalysis(analysisId) {
  const poll = async () => {
    try {
      const resp = await fetch('/api/analyze/' + analysisId);
      const data = await resp.json();

      // Update progress
      const log = document.getElementById('progressLog');
      log.innerHTML = '';
      data.progress.forEach(msg => addProgress(msg, 'step'));

      if (data.status === 'complete') {
        addProgress('Analysis complete!', 'done');
        document.getElementById('progressSpinner').style.display = 'none';
        document.getElementById('analyzeBtn').disabled = false;
        showResults({ result: data.result, markdown: data.markdown, graph_data: data.graph_data });
        return;
      } else if (data.status === 'error') {
        addProgress('Error: ' + (data.error || 'Unknown'), 'error');
        document.getElementById('progressSpinner').style.display = 'none';
        document.getElementById('analyzeBtn').disabled = false;
        return;
      }

      setTimeout(poll, 2000);
    } catch (e) {
      addProgress('Polling error: ' + e.message, 'error');
    }
  };
  poll();
}

function showResults(data) {
  const results = document.getElementById('results');
  results.classList.add('active');

  // Render markdown report
  if (data.markdown) {
    document.getElementById('reportContent').innerHTML = marked.parse(data.markdown);
  }

  // Render JSON
  if (data.result) {
    document.getElementById('jsonContent').textContent = JSON.stringify(data.result, null, 2);
  }

  // Render graph
  if (data.graph_data && data.graph_data.nodes && data.graph_data.nodes.length > 0) {
    const container = document.getElementById('graph-container');
    const visData = {
      nodes: new vis.DataSet(data.graph_data.nodes),
      edges: new vis.DataSet(data.graph_data.edges),
    };
    const options = {
      nodes: { shape: 'dot', size: 16, font: { color: '#c9d1d9', size: 12 } },
      edges: { color: { color: '#30363d', highlight: '#58a6ff' }, font: { color: '#8b949e', size: 10 } },
      physics: { solver: 'forceAtlas2Based', forceAtlas2Based: { gravitationalConstant: -30 } },
      interaction: { hover: true, tooltipDelay: 100 },
      layout: { improvedLayout: true },
    };
    new vis.Network(container, visData, options);
  }
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

function switchTab(el) {
  document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
  document.querySelectorAll('.tab-content').forEach(t => t.classList.remove('active'));
  el.classList.add('active');
  document.getElementById('tab-' + el.dataset.tab).classList.add('active');

  // Re-fit graph if switching to graph tab
  if (el.dataset.tab === 'graph') {
    const container = document.getElementById('graph-container');
    container.style.height = '500px';
  }
}

// Enter key submits
document.getElementById('query').addEventListener('keydown', (e) => {
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault();
    startAnalysis();
  }
});
</script>
</body>
</html>"""
