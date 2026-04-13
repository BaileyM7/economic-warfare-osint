"""Briefings router — CRUD + AI generation for intelligence briefings."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from src.common.config import config
from src.db import get_db, log_activity, _now, _new_id, row_to_briefing, row_to_coa
from src.llm import get_anthropic_client
from src.routers._shared import notify_monitoring

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["briefings"])


# --- Reference to the in-memory analyses dict (set by api.py) ---

_analyses_ref: dict[str, dict[str, Any]] | None = None


def set_analyses_ref(analyses: dict[str, dict[str, Any]]) -> None:
    global _analyses_ref
    _analyses_ref = analyses


# --- Request models ---


class BriefingCreateRequest(BaseModel):
    title: str
    type: str = "situation_update"
    reference_id: str | None = None
    content_markdown: str = ""


class BriefingGenerateRequest(BaseModel):
    coa_id: str | None = None
    analysis_id: str | None = None
    briefing_type: str = "situation_update"


class BriefingUpdateRequest(BaseModel):
    status: str | None = None
    title: str | None = None
    content_markdown: str | None = None


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


@router.post("/briefing/generate")
async def generate_briefing(req: BriefingGenerateRequest):
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

    system_prompt = (
        "You are an intelligence analyst producing a structured briefing document. "
        "Generate a Markdown briefing with these sections:\n"
        "## Situation\n## Analysis\n## Recommendation\n## Risk Assessment\n\n"
        "Be concise, specific, and reference concrete data from the source material. "
        "Use bullet points for key findings. Return ONLY the Markdown content."
    )
    user_content = f"Briefing type: {req.briefing_type}\n\nSource data:\n{json.dumps(source_data, default=str)}"

    try:
        response = await asyncio.wait_for(
            client.messages.create(
                model=config.model,
                max_tokens=2000,
                system=system_prompt,
                messages=[{"role": "user", "content": user_content}],
            ),
            timeout=30.0,
        )
        content_md = response.content[0].text.strip()
    except Exception as exc:
        logger.warning("Briefing generation failed: %s", exc)
        raise HTTPException(status_code=502, detail=f"LLM generation failed: {exc}")

    # Auto-create DB record
    now = _now()
    briefing_id = _new_id()
    ref_id = req.coa_id or req.analysis_id
    conn = get_db()
    try:
        conn.execute(
            "INSERT INTO briefings (id, title, type, status, reference_id, content_markdown, created_at, updated_at) "
            "VALUES (?, ?, ?, 'draft', ?, ?, ?, ?)",
            (briefing_id, source_title, req.briefing_type, ref_id, content_md, now, now),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM briefings WHERE id = ?", (briefing_id,)).fetchone()
    finally:
        conn.close()
    log_activity("briefing_generated", f"Briefing generated: {source_title}", related_id=briefing_id)
    await notify_monitoring("briefing_generated", f"Briefing generated: {source_title}")
    return row_to_briefing(row)


@router.get("/briefing")
async def list_briefings(status: str | None = None):
    conn = get_db()
    try:
        if status:
            rows = conn.execute("SELECT * FROM briefings WHERE status = ? ORDER BY updated_at DESC", (status,)).fetchall()
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
        conn.execute(f"UPDATE briefings SET {set_clause} WHERE id = ?", (*updates.values(), briefing_id))
        conn.commit()
        if req.status and req.status != existing["status"]:
            log_activity("briefing_status_changed", f"Briefing '{existing['title']}' status: {existing['status']} -> {req.status}", related_id=briefing_id)
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
    log_activity("briefing_deleted", f"Briefing deleted: {existing['title']}", related_id=briefing_id)
    await notify_monitoring("briefing_deleted", f"Briefing deleted: {existing['title']}")
    return {"detail": "deleted"}
