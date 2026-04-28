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
    sources: list[dict] = []
    rationale: str = ""


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
    sources: list[dict] | None = None
    rationale: str | None = None


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
            "source_analysis_id, recommendations, friendly_fire, expected_effects, sources, rationale, "
            "created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, 'draft', ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                coa_id, req.name, req.description, json.dumps(req.target_entities),
                req.action_type, req.confidence, req.source_analysis_id,
                json.dumps(req.recommendations), json.dumps(req.friendly_fire),
                json.dumps(req.expected_effects), json.dumps(req.sources),
                req.rationale, now, now,
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
        for field in ("name", "description", "action_type", "status", "confidence", "rationale"):
            val = getattr(req, field, None)
            if val is not None:
                updates[field] = val
        for field in ("target_entities", "recommendations", "friendly_fire", "expected_effects", "sources"):
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


def _coa_sources_from_analysis(analysis_data: dict | None) -> list[dict]:
    """Extract a normalized sources list from an analysis payload for COA citations.

    Preserves `record_url` and `description` so deep links (e.g., Sayari entity
    URLs) survive into the COA, and from there into briefings generated against
    that COA.
    """
    if not analysis_data:
        return []
    raw = analysis_data.get("sources_used") or analysis_data.get("sources") or []
    normalized: list[dict] = []
    for item in raw:
        if isinstance(item, str):
            normalized.append({"name": item})
        elif isinstance(item, dict):
            name = item.get("name") or item.get("source") or item.get("tool")
            if not name:
                continue
            entry: dict = {"name": str(name)}
            if item.get("url"):
                entry["url"] = str(item["url"])
            if item.get("record_url"):
                entry["record_url"] = str(item["record_url"])
            if item.get("description"):
                entry["description"] = str(item["description"])
            normalized.append(entry)
    return normalized


_ANALYST_COA_SYSTEM_PROMPT = """You are a senior economic-warfare and military strategist generating Courses of Action (COAs) for an analyst audience. The reader is a subject-matter analyst who will defend each COA to a supervisor and must trace every claim back to the source data.

A COA that says "do X" is useless. A COA that says "do X **because** evidence Y from source [N] shows Z" is what an analyst can defend. Every recommended action MUST carry a "because" supported by a numbered citation.

Generate 2-3 COA options. Return a JSON array of objects with THIS exact schema:

{
  "name": "short action-oriented name",
  "description": "2-3 sentence summary naming specific entities and mechanisms",
  "rationale": "The 'why' — 2-4 sentences explaining the analytical case for choosing this COA. Cite specific [N] markers from the available sources for every claim. Example: 'Beneficial ownership through Hong Kong shell entities creates a sanctions monitoring blind spot [3]; trade counterparty data shows systematic forced-labor exposure across Vietnam and China shipments [4]; combined with moderate AML risk in the corporate domicile [5], the threshold for enforcement action is met.'",
  "action_type": "sanction | export_control | asset_freeze | investment_screening | cyber | diplomatic | other",
  "target_entities": ["specific named entities from the analysis"],
  "recommendations": [
    "Specific action statement — Because: <one-sentence justification with [N] citation>"
  ],
  "expected_effects": [
    "Quantitative outcome — Because: <evidence link with [N]> [Confidence: HIGH|MEDIUM|LOW]"
  ],
  "friendly_fire": [
    {
      "entity": "named US/allied entity",
      "impact": "specific risk with magnitude",
      "confidence": "HIGH | MEDIUM | LOW",
      "because": "one-sentence reason this entity is exposed",
      "cite_ids": [<numeric ids from the available source list>]
    }
  ],
  "alternatives_considered": [
    "Alternative X considered but rejected because <reason with [N] citation>"
  ],
  "confidence": 0.0-1.0
}

HARD RULES:
- The `rationale` field is REQUIRED and must contain at least 2 distinct [N] markers drawn from the provided source list. Do not collapse all citations to [1].
- Every entry in `recommendations` MUST contain "Because:" followed by a justification with at least one [N] citation. A bare action like "Issue a Withhold Release Order" is REJECTED — write "Issue a Withhold Release Order against supplier X — Because: trade records flag forced-labor exposure on PSA shipments to Vietnam [3]".
- Every entry in `expected_effects` MUST contain "Because:" + [N] citation + "[Confidence: ...]".
- Every `friendly_fire` entry MUST include `because` (one sentence) and `cite_ids` (array of source numbers from the AVAILABLE SOURCES list).
- Every `alternatives_considered` entry MUST include "rejected because" with a [N] citation.
- Use ONLY [N] markers that exist in the provided AVAILABLE SOURCES list. If you cannot tie a claim to a provided source, omit the claim or label it "(unsourced — LOW confidence)".
- Name specific entities from the analysis — never generic ("the adversary", "a company").
- NO filler. Every sentence carries information.
- Return ONLY the JSON array. No markdown fences, no preamble."""


@router.post("/coa/generate")
async def generate_coa(req: COAGenerateRequest):
    client = get_anthropic_client()
    if not client:
        raise HTTPException(status_code=503, detail="Anthropic API key not configured")

    sources = _coa_sources_from_analysis(req.analysis_data)

    def _fmt_source_for_prompt(i: int, s: dict) -> str:
        line = f"[{i}] {s.get('name', 'unknown')}"
        if s.get("description"):
            line += f" — {s['description']}"
        if s.get("url"):
            line += f"\n    {s['url']}"
        if s.get("record_url"):
            line += f"\n    Record: {s['record_url']}"
        return line

    sources_block = (
        "\n".join(_fmt_source_for_prompt(i, s) for i, s in enumerate(sources, 1))
        if sources
        else "(No structured sources provided — cite by name from the analysis data and label them '(unsourced)' since no [N] mapping is available.)"
    )

    user_content = ""
    if req.objective:
        user_content += f"OBJECTIVE: {req.objective}\n\n"
    user_content += f"AVAILABLE SOURCES:\n{sources_block}\n\n"
    if req.analysis_data:
        user_content += f"ANALYSIS DATA:\n{json.dumps(req.analysis_data)}"
    if not req.objective and not req.analysis_data:
        user_content = "Generate general economic warfare COA options for Indo-Pacific region."

    try:
        response = await asyncio.wait_for(
            client.messages.create(
                model=config.model,
                # Bumped to 4500 to fit 2-3 COAs each carrying rationale,
                # because-justified recommendations, structured friendly_fire,
                # and alternatives_considered with cite markers.
                max_tokens=4500,
                system=_ANALYST_COA_SYSTEM_PROMPT,
                messages=[{"role": "user", "content": user_content}],
            ),
            timeout=60.0,
        )
        text = response.content[0].text.strip()
        parsed: list = []
        if text.startswith("["):
            parsed = json.loads(text)
        else:
            match = re.search(r'\[.*\]', text, re.DOTALL)
            if match:
                parsed = json.loads(match.group())
        if sources and isinstance(parsed, list):
            for coa in parsed:
                if isinstance(coa, dict) and "sources" not in coa:
                    coa["sources"] = sources
        return parsed
    except Exception as exc:
        logger.warning("COA generation failed: %s", exc)
        raise HTTPException(status_code=502, detail=f"LLM generation failed: {exc}")
