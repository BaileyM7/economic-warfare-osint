"""Orchestrator endpoints — tool inventory + async/sync analysis pipeline.

Extracted from src/api.py (Phase 2 Stage 3). Exposes the multi-agent
orchestrator: list available tools, kick off a background analysis (polled via
its analysis-id), fetch that analysis's status/result, and a synchronous
analyze-and-return variant. Async analyses write their status/progress/result
into the shared in-memory store in src.common.analyses (the SAME object the
briefings router reads via set_analyses_ref). Mounted at /api with require_auth
applied at include time (see src/api.py). Behaviour is unchanged from the
original inline handlers.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from src.common.analyses import ANALYSES_EVENTS_CAP as _ANALYSES_EVENTS_CAP
from src.common.analyses import ANALYSES_PROGRESS_CAP as _ANALYSES_PROGRESS_CAP
from src.common.analyses import analyses as _analyses
from src.common.cache import get_cached, set_cached
from src.orchestrator.main import Orchestrator
from src.orchestrator.tool_registry import ToolRegistry

router = APIRouter(prefix="/api", tags=["orchestrator"])

# Pre-warm cache: replay a prior run of the same question fast (no Claude/API
# calls → no 3–5 min wait, no rate-limit risk) while still animating the swarm.
# On by default for the demo; set EMISSARY_PREWARM_CACHE=0 to always run live.
_PREWARM = os.getenv("EMISSARY_PREWARM_CACHE", "1").lower() not in ("0", "false", "no")
_PREWARM_NS = "analysis_prewarm"
_PREWARM_TTL = 86400  # 24h


def _normalize_query(query: str) -> str:
    return " ".join(query.lower().split())


async def _replay_cached(analysis_id: str, cached: dict[str, Any], on_event, on_progress) -> None:
    """Replay a cached run's events (paced so the swarm animates) then set its result."""
    for ev in cached.get("events") or []:
        on_event(ev)
        # Pace so the board lights up over ~10s instead of resolving instantly.
        await asyncio.sleep(0.12 if ev.get("type") == "tool" else 0.3)
    entry = _analyses.get(analysis_id)
    if entry is not None:
        entry["result"] = cached.get("result")
        entry["status"] = "completed"
    on_progress("Analysis complete.")


# --- Request / Response models ---


class AnalyzeRequest(BaseModel):
    query: str


class AnalyzeResponse(BaseModel):
    analysis_id: str
    status: str


class AnalysisStatus(BaseModel):
    analysis_id: str
    status: str
    progress: list[str]
    # Structured agent-swarm events (plan / per-tool running|done). Drives the
    # live "swarm of AI agents" UI; see _run_analysis.on_event for the shapes.
    events: list[dict[str, Any]] = []
    result: dict[str, Any] | None = None
    markdown: str | None = None
    graph_data: dict[str, Any] | None = None
    error: str | None = None


@router.get("/tools")
async def list_tools():
    registry = ToolRegistry()
    await registry._ensure_loaded()
    return {"tools": registry.list_tools()}


async def _run_analysis(analysis_id: str, query: str) -> None:
    def on_progress(msg: str) -> None:
        entry = _analyses.get(analysis_id)
        if entry is None:
            return  # evicted by TTL/LRU mid-run; drop further updates
        progress = entry["progress"]
        progress.append(msg)
        if len(progress) > _ANALYSES_PROGRESS_CAP:
            del progress[:-_ANALYSES_PROGRESS_CAP]

    def on_event(event: dict[str, Any]) -> None:
        entry = _analyses.get(analysis_id)
        if entry is None:
            return
        events = entry["events"]
        events.append(event)
        if len(events) > _ANALYSES_EVENTS_CAP:
            del events[:-_ANALYSES_EVENTS_CAP]

    qn = _normalize_query(query)
    if _PREWARM:
        cached = get_cached(_PREWARM_NS, q=qn)
        if isinstance(cached, dict) and cached.get("result"):
            await _replay_cached(analysis_id, cached, on_event, on_progress)
            return

    on_progress("Starting analysis pipeline...")
    try:
        orchestrator = Orchestrator()
        assessment = await orchestrator.analyze(
            query, progress_callback=on_progress, event_callback=on_event
        )
        entry = _analyses.get(analysis_id)
        if entry is not None:
            entry["result"] = assessment.model_dump(mode="json")
            entry["status"] = "completed"
            if _PREWARM:
                set_cached(
                    {"events": list(entry.get("events") or []), "result": entry["result"]},
                    _PREWARM_NS,
                    ttl=_PREWARM_TTL,
                    q=qn,
                )
        on_progress("Done.")
    except Exception as e:
        entry = _analyses.get(analysis_id)
        if entry is not None:
            entry["status"] = "failed"
            entry["error"] = str(e)
        on_progress(f"Error: {e}")


@router.post("/analyze", response_model=AnalyzeResponse)
async def start_analysis(req: AnalyzeRequest):
    query = (req.query or "").strip()
    if not query:
        raise HTTPException(status_code=400, detail="Query cannot be empty")

    analysis_id = uuid.uuid4().hex[:8]
    _analyses[analysis_id] = {
        "analysis_id": analysis_id,
        "status": "running",
        "progress": ["Queued"],
        "events": [],
        "result": None,
        "markdown": None,
        "graph_data": None,
        "error": None,
    }
    asyncio.create_task(_run_analysis(analysis_id, query))
    return {"analysis_id": analysis_id, "status": "running"}


@router.get("/analyze/{analysis_id}", response_model=AnalysisStatus)
async def get_analysis(analysis_id: str):
    status = _analyses.get(analysis_id)
    if not status:
        raise HTTPException(status_code=404, detail="Analysis not found")
    return status


@router.post("/analyze/sync")
async def analyze_sync(req: AnalyzeRequest):
    query = (req.query or "").strip()
    if not query:
        raise HTTPException(status_code=400, detail="Query cannot be empty")

    orchestrator = Orchestrator()
    assessment = await orchestrator.analyze(query)
    return JSONResponse(content=assessment.model_dump(mode="json"))
