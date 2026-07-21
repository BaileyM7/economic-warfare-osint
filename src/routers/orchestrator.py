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
import logging
import os
import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from src.auth import require_auth
from src.common import agent_memory
from src.common.analyses import get_analysis_store
from src.common.cache import get_cached, set_cached
from src.orchestrator.main import Orchestrator
from src.orchestrator.tool_registry import ToolRegistry

logger = logging.getLogger(__name__)

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
# The canonical demo questions (see docs/demo-runbook.md). Seeded as a constant
# floor so suggestions work on a fresh box before the registry is populated.
_DEMO_QUERIES = [
    "What happens to global semiconductor supply if we sanction Fujian Jinhua?",
    "Who ultimately owns Nuctech, and what are its sanctions exposures?",
    "How exposed is the drone supply chain to a DJI export ban?",
    "Map Rosatom’s subsidiaries and their Western trade links.",
]


def _normalize_query(query: str) -> str:
    return " ".join(query.lower().split())


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


async def _suggest_query(query: str) -> tuple[str | None, float, str]:
    """Best warmed query for a near-miss: ``(suggestion, score, backend)``.

    Semantic when embeddings are available (a *reworded* demo question now matches,
    not just a token-overlapping one), else the original Jaccard. Either way this
    is suggest-only — it never auto-answers a near-miss with a different question's
    assessment; the exact/auto-replay paths handle answering.

    Delegates to src/common/semantic_cache.py, the single home for query matching.
    """
    from src.common import semantic_cache

    match = await semantic_cache.match_query(query, _warmed_queries())
    if match.band in ("suggest", "auto_replay") and match.query is not None:
        # An auto_replay-band match is still a fine *suggestion* to surface here;
        # the suggest endpoint never answers, so the entity-signature guard that
        # gates auto-replay isn't needed for merely offering it.
        return match.query, round(match.similarity, 3), match.backend
    return None, 0.0, match.backend


async def _replay_cached(analysis_id: str, cached: dict[str, Any], on_event, on_progress) -> None:
    """Replay a cached run's events (paced so the swarm animates) then set its result."""
    for ev in cached.get("events") or []:
        on_event(ev)
        # Pace so the board lights up over ~10s instead of resolving instantly.
        await asyncio.sleep(0.12 if ev.get("type") == "tool" else 0.3)
    get_analysis_store().set_fields(analysis_id, result=cached.get("result"), status="completed")
    on_progress("Analysis complete.")


# --- Request / Response models ---


class AnalyzeRequest(BaseModel):
    query: str
    # Continue an existing thread. Optional and additive: omit it (as today's
    # frontend does) and a fresh session is minted per analysis, which is exactly
    # the current behaviour.
    session_id: str | None = None
    # Opt out of semantic auto-replay for this request — always run the question
    # live. The "Run this exact question instead" button posts this. Additive.
    force_fresh: bool = False


class AnalyzeResponse(BaseModel):
    analysis_id: str
    status: str
    # Additive — old clients ignore it. Pass it back on the next /analyze or
    # /followup to keep the thread.
    session_id: str | None = None


class SuggestResponse(BaseModel):
    # The nearest fast-replay query if the input is a close-but-not-exact match,
    # else null. The UI offers it as a "Did you mean…?" the user must confirm —
    # we never auto-answer a near-miss with a different question's assessment.
    suggestion: str | None = None
    score: float = 0.0
    # "vector" (semantic) or "lexical" (Jaccard fallback) — lets the UI/ops see
    # which path produced the suggestion without guessing.
    backend: str = "lexical"


class AnalysisStatus(BaseModel):
    analysis_id: str
    status: str
    session_id: str | None = None  # additive; lets a poller pick up the thread
    # Set when this run was answered by a semantically-equivalent WARMED question
    # rather than the exact one asked. {"query": <warmed>, "similarity": float}.
    # The UI must disclose this and offer "run the exact question instead".
    replayed_from: dict[str, Any] | None = None
    notice: str | None = None
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


def _remember_run(
    session_id: str | None,
    user_id: str | None,
    query: str,
    analysis_id: str,
    assessment: dict[str, Any] | None,
) -> None:
    """Record a completed run in the thread's working memory.

    Best-effort by construction: memory is an enhancement, the assessment is the
    product, so nothing here may fail an analysis.

    Called from BOTH the live and the cached-replay paths. Missing the replay path
    would make a warm demo question silently produce an amnesiac session — the
    follow-up would have no idea what was just asked, and only on the questions
    most likely to be demoed.
    """
    if not session_id or not user_id or not assessment:
        return
    try:
        agent_memory.append_turn(
            session_id, user_id, agent_memory.Turn(role="user", text=query, analysis_id=analysis_id)
        )
        summary = (assessment.get("executive_summary") or "").strip()
        if summary:
            agent_memory.append_turn(
                session_id,
                user_id,
                agent_memory.Turn(role="assistant", text=summary, analysis_id=analysis_id),
            )
        agent_memory.set_run_state(
            session_id,
            user_id,
            assessment=assessment,
            entities=agent_memory.entities_from_assessment(assessment),
            context_type="orchestrator",
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not record working memory for %s: %s", analysis_id, exc)


def _extract_memories_bg(
    query: str,
    assessment: dict[str, Any] | None,
    user_id: str | None,
    session_id: str | None,
    analysis_id: str,
) -> None:
    """Schedule background long-term-memory extraction. Never blocks/raises.

    Extraction is a 3-5s Haiku call; it runs as a detached task so the analyst is
    never made to wait, and a failure only means "no new memories", never a failed
    analysis. Skipped entirely without a user (nothing to scope memory to).
    """
    if not user_id or not assessment:
        return

    async def _do() -> None:
        try:
            from src.orchestrator.memory_extract import extract_and_remember

            n = await extract_and_remember(
                query=query,
                assessment=assessment,
                user_id=user_id,
                session_id=session_id,
                analysis_id=analysis_id,
            )
            if n:
                logger.info("Extracted %d new long-term memories from %s", n, analysis_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Background memory extraction failed for %s: %s", analysis_id, exc)

    try:
        asyncio.create_task(_do())
    except RuntimeError:
        pass  # no running loop (shouldn't happen in the request path)


async def _try_semantic_replay(
    analysis_id: str, query: str, on_event, on_progress
) -> dict[str, Any] | None:
    """If a semantically-equivalent WARMED question exists, replay it. Else None.

    Only fires in the ``auto_replay`` band — low distance AND identical entity
    signature AND EMISSARY_SEMANTIC_REPLAY=1 — so it can never serve a different
    company's assessment for a subtly different question (the Nuctech→Hikvision
    trap). Returns the replayed result dict on success; None otherwise.

    On a hit, the FIRST thing surfaced is the disclosure, so the analyst sees what
    they're being given before they read it.
    """
    from src.common import semantic_cache

    match = await semantic_cache.match_query(query, _warmed_queries())
    if match.band != "auto_replay" or not match.query:
        return None

    cached = get_cached(_PREWARM_NS, q=_normalize_query(match.query))
    if not (isinstance(cached, dict) and cached.get("result")):
        return None

    pct = round(match.similarity * 100)
    on_event(
        {
            "type": "replay_notice",
            "replayed_from": match.query,
            "similarity": match.similarity,
            "message": (
                f"Answering the previously-run, semantically equivalent question "
                f"“{match.query}” ({pct}% match). Run the exact question instead?"
            ),
        }
    )
    on_progress(f"Replaying semantically equivalent warmed question ({pct}% match).")

    await _replay_cached(analysis_id, cached, on_event, on_progress)
    get_analysis_store().set_fields(
        analysis_id,
        replayed_from={"query": match.query, "similarity": match.similarity},
        notice=(
            f"Answered from a semantically equivalent warmed question "
            f"(“{match.query}”, {pct}% match). Re-run for the exact question."
        ),
    )
    return cached.get("result")


async def _run_analysis(
    analysis_id: str,
    query: str,
    session_id: str | None = None,
    user_id: str | None = None,
    force_fresh: bool = False,
) -> None:
    store = get_analysis_store()

    def on_progress(msg: str) -> None:
        # append_progress is a no-op if the entry was evicted (TTL/LRU) mid-run,
        # and caps the list server-side; no read-modify-write here.
        store.append_progress(analysis_id, msg)

    def on_event(event: dict[str, Any]) -> None:
        store.append_event(analysis_id, event)

    qn = _normalize_query(query)
    if _PREWARM:
        cached = get_cached(_PREWARM_NS, q=qn)
        if isinstance(cached, dict) and cached.get("result"):
            await _replay_cached(analysis_id, cached, on_event, on_progress)
            # A replayed run is still a turn in the thread — see _remember_run.
            _remember_run(session_id, user_id, query, analysis_id, cached.get("result"))
            return

        # No exact hit — try a semantic auto-replay (opt-in + entity-signature
        # gated). force_fresh (the "run the exact question" button) skips this.
        if not force_fresh:
            replayed = await _try_semantic_replay(analysis_id, query, on_event, on_progress)
            if replayed is not None:
                _remember_run(session_id, user_id, query, analysis_id, replayed)
                return

    on_progress("Starting analysis pipeline...")
    try:
        orchestrator = Orchestrator()
        assessment = await orchestrator.analyze(
            query,
            progress_callback=on_progress,
            event_callback=on_event,
            user_id=user_id,
            session_id=session_id,
        )
        result = assessment.model_dump(mode="json")
        store.set_fields(analysis_id, result=result, status="completed")
        if _PREWARM:
            snapshot = store.get(analysis_id) or {}
            set_cached(
                {"events": list(snapshot.get("events") or []), "result": result},
                _PREWARM_NS,
                ttl=_PREWARM_TTL,
                q=qn,
            )
            # Track the original query so "Did you mean…?" can suggest it later.
            _register_warmed_query(query)
        _remember_run(session_id, user_id, query, analysis_id, result)
        # Fire-and-forget: extract durable facts into long-term memory. Runs
        # AFTER the result is stored and never blocks or fails the analysis.
        _extract_memories_bg(query, result, user_id, session_id, analysis_id)
        on_progress("Done.")
    except Exception as e:
        store.set_fields(analysis_id, status="failed", error=str(e))
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
    suggestion, score, backend = await _suggest_query(query)
    return SuggestResponse(suggestion=suggestion, score=score, backend=backend)


@router.post("/analyze", response_model=AnalyzeResponse)
async def start_analysis(req: AnalyzeRequest, username: str = Depends(require_auth)):
    query = (req.query or "").strip()
    if not query:
        raise HTTPException(status_code=400, detail="Query cannot be empty")

    # Continue the caller's thread when they pass a live one; otherwise start a
    # new thread. An unknown/expired/someone-else's id silently becomes a new
    # session rather than a 4xx — a stale tab shouldn't be an error.
    session_id = req.session_id or ""
    if not session_id or agent_memory.get_working(session_id, username) is None:
        session_id = agent_memory.new_session(username)

    analysis_id = uuid.uuid4().hex[:8]
    get_analysis_store().create(
        analysis_id,
        {
            "analysis_id": analysis_id,
            "status": "running",
            "session_id": session_id,
            "replayed_from": None,
            "notice": None,
            "progress": ["Queued"],
            "events": [],
            "result": None,
            "markdown": None,
            "graph_data": None,
            "error": None,
        },
    )
    asyncio.create_task(
        _run_analysis(analysis_id, query, session_id, username, force_fresh=req.force_fresh)
    )
    return {"analysis_id": analysis_id, "status": "running", "session_id": session_id}


@router.get("/analyze/{analysis_id}", response_model=AnalysisStatus)
async def get_analysis(analysis_id: str):
    status = get_analysis_store().get(analysis_id)
    if not status:
        raise HTTPException(status_code=404, detail="Analysis not found")
    return status


@router.post("/analyze/sync")
async def analyze_sync(req: AnalyzeRequest, username: str = Depends(require_auth)):
    query = (req.query or "").strip()
    if not query:
        raise HTTPException(status_code=400, detail="Query cannot be empty")

    orchestrator = Orchestrator()
    assessment = await orchestrator.analyze(query)
    result = assessment.model_dump(mode="json")

    # Thread this run too, but only when the caller opted in with a session_id —
    # the sync endpoint is used for one-shot scripted calls, so minting a session
    # per request would fill the store with threads nobody continues.
    if req.session_id and agent_memory.get_working(req.session_id, username) is not None:
        _remember_run(req.session_id, username, query, "sync", result)

    return JSONResponse(content=result)
