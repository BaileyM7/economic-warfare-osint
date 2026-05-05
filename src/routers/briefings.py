"""Briefings router — CRUD + AI generation for intelligence briefings."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field

from src.common.config import config
from src.common.rate_limit import LLM_GENERATE_LIMIT, limiter
from src.common.sanitize import sanitize_for_llm
from src.db import get_db, log_activity, _now, _new_id, row_to_briefing, row_to_coa
from src.llm import get_anthropic_client
from src.routers._shared import notify_monitoring

# Allow alphanumerics, dash, underscore — matches `_new_id` and UUID outputs.
_ID_PATTERN = r"^[a-zA-Z0-9_-]+$"

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["briefings"])


# --- Reference to the in-memory analyses dict (set by api.py) ---

_analyses_ref: dict[str, dict[str, Any]] | None = None


def set_analyses_ref(analyses: dict[str, dict[str, Any]]) -> None:
    global _analyses_ref
    _analyses_ref = analyses


# --- Request models ---


class BriefingCreateRequest(BaseModel):
    title: str = Field(..., min_length=1, max_length=200)
    type: str = Field("situation_update", max_length=50, pattern=r"^[a-zA-Z0-9_-]+$")
    reference_id: str | None = Field(None, max_length=64, pattern=_ID_PATTERN)
    content_markdown: str = Field("", max_length=50_000)


class BriefingGenerateRequest(BaseModel):
    coa_id: str | None = Field(None, max_length=64, pattern=_ID_PATTERN)
    analysis_id: str | None = Field(None, max_length=64, pattern=_ID_PATTERN)
    briefing_type: str = Field("situation_update", max_length=50, pattern=r"^[a-zA-Z0-9_-]+$")


class BriefingUpdateRequest(BaseModel):
    status: str | None = Field(None, max_length=32, pattern=r"^[a-zA-Z0-9_-]+$")
    title: str | None = Field(None, min_length=1, max_length=200)
    content_markdown: str | None = Field(None, max_length=50_000)


# --- Endpoints ---


@router.post("/briefing")
async def create_briefing(req: BriefingCreateRequest):
    now = _now()
    briefing_id = _new_id()
    conn = get_db()
    try:
        conn.execute(
            "INSERT INTO briefings (id, title, type, status, reference_id, content_markdown, created_at, updated_at) "
            "VALUES (?, ?, ?, 'draft', ?, ?, ?, ?)",
            (briefing_id, req.title, req.type, req.reference_id, req.content_markdown, now, now),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM briefings WHERE id = ?", (briefing_id,)).fetchone()
    finally:
        conn.close()
    log_activity("briefing_created", f"Briefing created: {req.title}", related_id=briefing_id)
    await notify_monitoring("briefing_created", f"Briefing created: {req.title}")
    return row_to_briefing(row)


def _extract_sources(source_data: dict[str, Any]) -> list[dict[str, Any]]:
    """Pull a structured sources list from an analysis result or COA payload.

    Handles the orchestrator's `sources_used` / `sources` fields (list of strings
    or dicts) and normalizes to a list of {name, url?, accessed_at?} dicts.
    """
    raw = None
    for key in ("sources_used", "sources"):
        if key in source_data and source_data[key]:
            raw = source_data[key]
            break
    if not raw:
        return []

    normalized: list[dict[str, Any]] = []
    for item in raw:
        if isinstance(item, str):
            normalized.append({"name": item})
        elif isinstance(item, dict):
            name = item.get("name") or item.get("source") or item.get("tool")
            if not name:
                continue
            entry: dict[str, Any] = {"name": str(name)}
            if item.get("url"):
                entry["url"] = str(item["url"])
            if item.get("record_url"):
                entry["record_url"] = str(item["record_url"])
            if item.get("accessed_at"):
                entry["accessed_at"] = str(item["accessed_at"])
            if item.get("description"):
                entry["description"] = str(item["description"])
            normalized.append(entry)
    return normalized


def _format_sources_for_prompt(sources: list[dict[str, Any]]) -> str:
    """Format the sources list for the LLM prompt as `[N] Name — description` plus
    indented URL lines. The LLM is instructed to mirror this exact shape verbatim
    in the rendered ## Sources section so that records and APIs stay distinct.
    """
    if not sources:
        return "(No structured source list provided — infer sources from the data payload below and cite them by name.)"
    lines = []
    for i, s in enumerate(sources, start=1):
        head = f"[{i}] {s.get('name', 'unknown')}"
        if s.get("description"):
            head += f" — {s['description']}"
        lines.append(head)
        if s.get("url"):
            lines.append(f"    {s['url']}")
        if s.get("record_url"):
            lines.append(f"    Record: {s['record_url']}")
    return "\n".join(lines)


_ANALYST_BRIEFING_SYSTEM_PROMPT = """You are a senior intelligence analyst producing a briefing for a customer analyst audience (NOT an executive summary). The reader is a subject-matter analyst who needs to assess confidence, trace claims to evidence, and defend conclusions to a supervisor.

Produce a Markdown briefing with exactly these sections in this order:

## Executive Summary
2-4 sentences. Name specific entities, quantities, and timeframes. No filler.

## Target Assessment
The entity/sector/scenario under analysis. Include what is known with certainty vs. what is inferred.

## Key Findings
A numbered list. Each finding has this exact structure:
  **N. [Finding title]** — [Confidence: HIGH | MEDIUM | LOW]
  Claim: [one-sentence claim with concrete entities/numbers]
  Evidence: [what in the source data supports this] [citation marker]
  Reasoning: [why this evidence supports the claim; note assumptions]

Use inline citation markers like `[1]`, `[2]` that reference the `## Sources` section.

## Risk & Friendly Fire
Named second-order risks and friendly-fire exposure. Each risk carries confidence and a citation.

## Recommendations
Each recommendation links back to a specific finding by number (e.g., "Per Finding 2, ..."). Avoid generic advice.

## Sources
A numbered list matching the inline `[N]` markers. **You MUST reproduce the exact source list provided in the user message verbatim** — same numbering, same names, same URLs, same record links. Do NOT invent new sources, do NOT collapse multiple distinct APIs into a single rolled-up label, and do NOT rename them.

The required format for each source entry is:
  [N] Source name — short description of what was drawn from it
      <api or product URL>
      Record: <entity-specific deep link>          ← include this line ONLY when the user-supplied source list provides a `Record:` URL for that entry

A real example (do not copy verbatim — use whatever the user message gives you):
  [1] Sayari Graph — Beneficial ownership chain for LUSTER MARITIME SA
      https://app.sayari.com/
      Record: https://app.sayari.com/entities/abc123
  [2] OFAC SDN — Sanctions screening
      https://sanctionssearch.ofac.treas.gov/

HARD RULES:
- Every factual claim in Key Findings and Risk & Friendly Fire MUST carry a `[N]` citation that maps to a distinct entry in the user-supplied source list.
- If the user-supplied list contains 3 sources, you MUST use at least 3 different `[N]` markers across the briefing — do not point every claim at `[1]`.
- Do NOT cite a source as `[N]` if it is not in the user-supplied list. If you genuinely cannot tie a claim to a provided source, omit the claim or downgrade it to LOW confidence and label it "(unsourced)".
- Every finding and risk MUST carry a HIGH/MEDIUM/LOW confidence label.
- Name specific entities (companies, vessels, people, ports, tickers). Never write "a company" or "the region" when a name is available in the source data.
- Quote key numbers from the source data verbatim.
- NO filler phrases ("It is important to note", "In conclusion", "Overall").
- NO generic recommendations ("monitor the situation", "consider sanctions").
- Return ONLY the Markdown. No preamble, no code fences."""


@router.post("/briefing/generate")
@limiter.limit(LLM_GENERATE_LIMIT)
async def generate_briefing(request: Request, response: Response, req: BriefingGenerateRequest):
    client = get_anthropic_client()
    if not client:
        raise HTTPException(status_code=503, detail="Anthropic API key not configured")

    source_data: dict[str, Any] = {}
    source_title = "Generated Briefing"

    # Gather source data from COA or analysis
    if req.coa_id:
        conn = get_db()
        try:
            coa_row = conn.execute("SELECT * FROM coas WHERE id = ?", (req.coa_id,)).fetchone()
        finally:
            conn.close()
        if coa_row:
            source_data = row_to_coa(coa_row)
            source_title = f"Briefing: {coa_row['name']}"
    elif req.analysis_id and _analyses_ref and req.analysis_id in _analyses_ref:
        analysis = _analyses_ref[req.analysis_id]
        source_data = analysis.get("result", {})
        source_title = f"Briefing: Analysis {req.analysis_id}"

    sources = _extract_sources(source_data)
    # Fallback: COAs created before source plumbing existed (or via the manual
    # POST /coa path that bypasses /coa/generate) won't have sources attached.
    # If we have a `source_analysis_id` AND that analysis is still in memory,
    # pull sources from it. This means re-generating a briefing for an old COA
    # produces real per-source citations as long as the underlying analysis
    # is still cached.
    if not sources and req.coa_id:
        analysis_id = source_data.get("source_analysis_id")
        if analysis_id and _analyses_ref and analysis_id in _analyses_ref:
            cached = _analyses_ref[analysis_id].get("result", {})
            sources = _extract_sources(cached)
    sources_block = _format_sources_for_prompt(sources)

    user_content = (
        f"Briefing type: {req.briefing_type}\n\n"
        f"Available sources (use these numbers as inline citations):\n{sources_block}\n\n"
        f"Source data payload (JSON):\n{json.dumps(source_data, default=str)}"
    )
    user_content = sanitize_for_llm(user_content, max_chars=60_000)

    try:
        response = await asyncio.wait_for(
            client.messages.create(
                model=config.model,
                max_tokens=3000,
                system=_ANALYST_BRIEFING_SYSTEM_PROMPT,
                messages=[{"role": "user", "content": user_content}],
            ),
            timeout=45.0,
        )
        content_md = response.content[0].text.strip()
    except Exception as exc:
        logger.warning("Briefing generation failed: %s", exc)
        raise HTTPException(status_code=502, detail=f"LLM generation failed: {exc}")

    # Auto-create DB record
    now = _now()
    briefing_id = _new_id()
    ref_id = req.coa_id or req.analysis_id
    sources_json = json.dumps(sources)
    conn = get_db()
    try:
        conn.execute(
            "INSERT INTO briefings (id, title, type, status, reference_id, content_markdown, sources, created_at, updated_at) "
            "VALUES (?, ?, ?, 'draft', ?, ?, ?, ?, ?)",
            (
                briefing_id,
                source_title,
                req.briefing_type,
                ref_id,
                content_md,
                sources_json,
                now,
                now,
            ),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM briefings WHERE id = ?", (briefing_id,)).fetchone()
    finally:
        conn.close()
    log_activity(
        "briefing_generated", f"Briefing generated: {source_title}", related_id=briefing_id
    )
    await notify_monitoring("briefing_generated", f"Briefing generated: {source_title}")
    return row_to_briefing(row)


@router.get("/briefing")
async def list_briefings(status: str | None = None):
    conn = get_db()
    try:
        if status:
            rows = conn.execute(
                "SELECT * FROM briefings WHERE status = ? ORDER BY updated_at DESC", (status,)
            ).fetchall()
        else:
            rows = conn.execute("SELECT * FROM briefings ORDER BY updated_at DESC").fetchall()
    finally:
        conn.close()
    return [row_to_briefing(r) for r in rows]


@router.get("/briefing/{briefing_id}")
async def get_briefing(briefing_id: str):
    conn = get_db()
    try:
        row = conn.execute("SELECT * FROM briefings WHERE id = ?", (briefing_id,)).fetchone()
    finally:
        conn.close()
    if not row:
        raise HTTPException(status_code=404, detail="Briefing not found")
    return row_to_briefing(row)


@router.put("/briefing/{briefing_id}")
async def update_briefing(briefing_id: str, req: BriefingUpdateRequest):
    conn = get_db()
    try:
        existing = conn.execute("SELECT * FROM briefings WHERE id = ?", (briefing_id,)).fetchone()
        if not existing:
            raise HTTPException(status_code=404, detail="Briefing not found")
        updates = {}
        for field in ("status", "title", "content_markdown"):
            val = getattr(req, field, None)
            if val is not None:
                updates[field] = val
        if not updates:
            return row_to_briefing(existing)
        updates["updated_at"] = _now()
        set_clause = ", ".join(f"{k} = ?" for k in updates)
        conn.execute(
            f"UPDATE briefings SET {set_clause} WHERE id = ?", (*updates.values(), briefing_id)
        )
        conn.commit()
        if req.status and req.status != existing["status"]:
            log_activity(
                "briefing_status_changed",
                f"Briefing '{existing['title']}' status: {existing['status']} -> {req.status}",
                related_id=briefing_id,
            )
        row = conn.execute("SELECT * FROM briefings WHERE id = ?", (briefing_id,)).fetchone()
    finally:
        conn.close()
    await notify_monitoring("briefing_updated", f"Briefing updated: {briefing_id}")
    return row_to_briefing(row)


@router.delete("/briefing/{briefing_id}")
async def delete_briefing(briefing_id: str):
    conn = get_db()
    try:
        existing = conn.execute("SELECT * FROM briefings WHERE id = ?", (briefing_id,)).fetchone()
        if not existing:
            raise HTTPException(status_code=404, detail="Briefing not found")
        conn.execute("DELETE FROM briefings WHERE id = ?", (briefing_id,))
        conn.commit()
    finally:
        conn.close()
    log_activity(
        "briefing_deleted", f"Briefing deleted: {existing['title']}", related_id=briefing_id
    )
    await notify_monitoring("briefing_deleted", f"Briefing deleted: {existing['title']}")
    return {"detail": "deleted"}
