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
_PREWARM_NS = "analysis_prewarm_v2"  # v2: invalidate pre-fix entries (opaque-source filter)
_PREWARM_TTL = 86400  # 24h

# Registry of queries we have a fast (cached) replay for. Used ONLY to power the
# safe "Did you mean…?" suggestion — we never auto-answer a near-miss, we only
# suggest the exact warmed query for the user to confirm. Stored as original-cased
# strings so the suggestion reads naturally.
_REGISTRY_KEY = "warmed_query_registry"
_REGISTRY_MAX = 200
# Similarity at/above which we surface a suggestion. High enough that only a
# genuine rewording of a warmed question matches — it's a suggestion, not a replay.
_SUGGEST_THRESHOLD = 0.6
# The canonical demo questions (see docs/demo-runbook.md). Seeded as a constant
# floor so suggestions work on a fresh box before the registry is populated.
_DEMO_QUERIES = [
    "What happens to global semiconductor supply if we sanction Fujian Jinhua?",
    "Who ultimately owns Nuctech, and what are its sanctions exposures?",
    "How exposed is the drone supply chain to a DJI export ban?",
    "Map Rosatom’s subsidiaries and their Western trade links.",
]
# Low-signal tokens dropped before comparing queries so structural filler
# ("what happens if we …") doesn't inflate the overlap score.
_STOPWORDS = frozenset(
    "a an the of to in on for and or if we our is are be do does what who how "
    "their its it this that with from into about as at by".split()
)


def _normalize_query(query: str) -> str:
    return " ".join(query.lower().split())


def _tokenize(query: str) -> set[str]:
    """Content tokens of a query: lowercased alphanumerics, stopwords removed."""
    import re

    toks = re.findall(r"[a-z0-9]+", query.lower())
    return {t for t in toks if t not in _STOPWORDS and len(t) > 1}


def _query_similarity(a: str, b: str) -> float:
    """Jaccard overlap of two queries' content tokens (0.0–1.0).

    Pure and dependency-free, isolated so it can later be swapped for an
    embedding-based score without touching callers.
    """
    ta, tb = _tokenize(a), _tokenize(b)
    if not ta or not tb:
        return 0.0
    inter = len(ta & tb)
    union = len(ta | tb)
    return inter / union if union else 0.0


def _warmed_queries() -> list[str]:
    """Known fast-replay queries: the demo floor plus anything we've cached."""
    seen: dict[str, str] = {}
    for q in _DEMO_QUERIES:
        seen.setdefault(_normalize_query(q), q)
    if _PREWARM:
        registry = get_cached(_PREWARM_NS, registry=_REGISTRY_KEY)
        if isinstance(registry, list):
            for q in registry:
                if isinstance(q, str) and q.strip():
                    seen.setdefault(_normalize_query(q), q)
    return list(seen.values())


def _register_warmed_query(query: str) -> None:
    """Record a query as fast-replayable (for the 'Did you mean' candidate set)."""
    if not _PREWARM:
        return
    qn = _normalize_query(query)
    registry = get_cached(_PREWARM_NS, registry=_REGISTRY_KEY)
    out: list[str] = list(registry) if isinstance(registry, list) else []
    if any(_normalize_query(q) == qn for q in out if isinstance(q, str)):
        return
    out.append(query)
    out = out[-_REGISTRY_MAX:]
    set_cached(out, _PREWARM_NS, ttl=_PREWARM_TTL, registry=_REGISTRY_KEY)


def _suggest_query(query: str) -> tuple[str | None, float]:
    """Best warmed query for a near-miss, or (None, 0.0).

    Never returns an exact match (those already instant-replay) — only a *different*
    warmed query similar enough to be worth confirming. The caller surfaces it as a
    suggestion; it is never auto-run.
    """
    qn = _normalize_query(query)
    best: str | None = None
    best_score = 0.0
    for cand in _warmed_queries():
        if _normalize_query(cand) == qn:
            continue  # exact — replay path already handles it
        score = _query_similarity(query, cand)
        if score > best_score:
            best, best_score = cand, score
    if best is not None and best_score >= _SUGGEST_THRESHOLD:
        return best, round(best_score, 3)
    return None, 0.0


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


class SuggestResponse(BaseModel):
    # The nearest fast-replay query if the input is a close-but-not-exact match,
    # else null. The UI offers it as a "Did you mean…?" the user must confirm —
    # we never auto-answer a near-miss with a different question's assessment.
    suggestion: str | None = None
    score: float = 0.0


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
                # Track the original query so "Did you mean…?" can suggest it later.
                _register_warmed_query(query)
        on_progress("Done.")
    except Exception as e:
        entry = _analyses.get(analysis_id)
        if entry is not None:
            entry["status"] = "failed"
            entry["error"] = str(e)
        on_progress(f"Error: {e}")


@router.post("/analyze/suggest", response_model=SuggestResponse)
async def suggest_analysis(req: AnalyzeRequest):
    """Return the nearest fast-replay query for a near-miss, else null.

    Safe by construction: a suggestion only, never an auto-answer. An exact match
    returns null (the analyze endpoint already instant-replays it).
    """
    query = (req.query or "").strip()
    if not query:
        return SuggestResponse(suggestion=None, score=0.0)
    suggestion, score = _suggest_query(query)
    return SuggestResponse(suggestion=suggestion, score=score)


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
