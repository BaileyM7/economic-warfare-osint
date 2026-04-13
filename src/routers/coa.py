"""COA Workspace router — CRUD + AI generation for Courses of Action."""

from __future__ import annotations

import asyncio
import json
import logging
import re

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from src.common.config import config
from src.db import get_db, log_activity, _now, _new_id, row_to_coa
from src.llm import get_anthropic_client
from src.routers._shared import notify_monitoring

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["coa"])

# --- Request models ---


class COACreateRequest(BaseModel):
    name: str
    description: str = ""
    target_entities: list[str] = []
    action_type: str = ""
    confidence: float | None = None
    source_analysis_id: str | None = None
    recommendations: list[str] = []
    friendly_fire: list[dict] = []
    expected_effects: list[str] = []


class COAUpdateRequest(BaseModel):
    name: str | None = None
    description: str | None = None
    target_entities: list[str] | None = None
    action_type: str | None = None
    status: str | None = None
    confidence: float | None = None
    recommendations: list[str] | None = None
    friendly_fire: list[dict] | None = None
    expected_effects: list[str] | None = None


class COAGenerateRequest(BaseModel):
    analysis_data: dict | None = None
    objective: str = ""


# --- Endpoints ---


@router.post("/coa")
async def create_coa(req: COACreateRequest):
    now = _now()
    coa_id = _new_id()
    conn = get_db()
    try:
        conn.execute(
            "INSERT INTO coas (id, name, description, target_entities, action_type, status, confidence, "
            "source_analysis_id, recommendations, friendly_fire, expected_effects, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, 'draft', ?, ?, ?, ?, ?, ?, ?)",
            (
                coa_id, req.name, req.description, json.dumps(req.target_entities),
                req.action_type, req.confidence, req.source_analysis_id,
                json.dumps(req.recommendations), json.dumps(req.friendly_fire),
                json.dumps(req.expected_effects), now, now,
            ),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM coas WHERE id = ?", (coa_id,)).fetchone()
    finally:
        conn.close()
    log_activity("coa_created", f"COA created: {req.name}", related_id=coa_id)
    await notify_monitoring("coa_created", f"COA created: {req.name}")
    return row_to_coa(row)


@router.get("/coa")
async def list_coas(status: str | None = None):
    conn = get_db()
    try:
        if status:
            rows = conn.execute("SELECT * FROM coas WHERE status = ? ORDER BY updated_at DESC", (status,)).fetchall()
        else:
            rows = conn.execute("SELECT * FROM coas ORDER BY updated_at DESC").fetchall()
    finally:
        conn.close()
    return [row_to_coa(r) for r in rows]


@router.get("/coa/{coa_id}")
async def get_coa(coa_id: str):
    conn = get_db()
    try:
        row = conn.execute("SELECT * FROM coas WHERE id = ?", (coa_id,)).fetchone()
    finally:
        conn.close()
    if not row:
        raise HTTPException(status_code=404, detail="COA not found")
    return row_to_coa(row)


@router.put("/coa/{coa_id}")
async def update_coa(coa_id: str, req: COAUpdateRequest):
    conn = get_db()
    try:
        existing = conn.execute("SELECT * FROM coas WHERE id = ?", (coa_id,)).fetchone()
        if not existing:
            raise HTTPException(status_code=404, detail="COA not found")
        updates = {}
        for field in ("name", "description", "action_type", "status", "confidence"):
            val = getattr(req, field, None)
            if val is not None:
                updates[field] = val
        for field in ("target_entities", "recommendations", "friendly_fire", "expected_effects"):
            val = getattr(req, field, None)
            if val is not None:
                updates[field] = json.dumps(val)
        if not updates:
            return row_to_coa(existing)
        updates["updated_at"] = _now()
        set_clause = ", ".join(f"{k} = ?" for k in updates)
        conn.execute(f"UPDATE coas SET {set_clause} WHERE id = ?", (*updates.values(), coa_id))
        conn.commit()
        if req.status and req.status != existing["status"]:
            log_activity("coa_status_changed", f"COA '{existing['name']}' status: {existing['status']} -> {req.status}", related_id=coa_id)
        row = conn.execute("SELECT * FROM coas WHERE id = ?", (coa_id,)).fetchone()
    finally:
        conn.close()
    await notify_monitoring("coa_updated", f"COA updated: {coa_id}")
    return row_to_coa(row)


@router.delete("/coa/{coa_id}")
async def delete_coa(coa_id: str):
    conn = get_db()
    try:
        existing = conn.execute("SELECT * FROM coas WHERE id = ?", (coa_id,)).fetchone()
        if not existing:
            raise HTTPException(status_code=404, detail="COA not found")
        conn.execute("DELETE FROM coas WHERE id = ?", (coa_id,))
        conn.commit()
    finally:
        conn.close()
    log_activity("coa_deleted", f"COA deleted: {existing['name']}", related_id=coa_id)
    await notify_monitoring("coa_deleted", f"COA deleted: {existing['name']}")
    return {"detail": "deleted"}


@router.post("/coa/generate")
async def generate_coa(req: COAGenerateRequest):
    client = get_anthropic_client()
    if not client:
        raise HTTPException(status_code=503, detail="Anthropic API key not configured")
    system_prompt = (
        "You are a military and economic warfare strategist. Given the analysis data and objective, "
        "generate 2-3 Courses of Action (COA) options. Return a JSON array of objects, each with: "
        "name (str), description (str), action_type (str), target_entities (list[str]), "
        "expected_effects (list[str]), friendly_fire (list[dict with 'entity' and 'impact' keys]), "
        "confidence (float 0-1). Return ONLY the JSON array, no markdown fences."
    )
    user_content = ""
    if req.objective:
        user_content += f"OBJECTIVE: {req.objective}\n\n"
    if req.analysis_data:
        user_content += f"ANALYSIS DATA:\n{json.dumps(req.analysis_data)}"
    if not user_content:
        user_content = "Generate general economic warfare COA options for Indo-Pacific region."
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
        text = response.content[0].text.strip()
        if text.startswith("["):
            return json.loads(text)
        match = re.search(r'\[.*\]', text, re.DOTALL)
        if match:
            return json.loads(match.group())
        return []
    except Exception as exc:
        logger.warning("COA generation failed: %s", exc)
        raise HTTPException(status_code=502, detail=f"LLM generation failed: {exc}")
