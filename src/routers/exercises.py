"""Exercise Control router — exercises, injects, delivery, scoring, assessment."""

from __future__ import annotations

import asyncio
import json
import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from src.common.config import config
from src.db import get_db, log_activity, _now, _new_id, row_to_exercise, row_to_inject
from src.llm import get_anthropic_client
from src.routers._shared import notify_monitoring

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["exercises"])


# --- Request models ---


class ExerciseCreateRequest(BaseModel):
    name: str


class ExerciseUpdateRequest(BaseModel):
    status: str


class InjectCreateRequest(BaseModel):
    inject_type: str
    target_groups: list[str] = []
    content: str = ""
    scheduled_offset: str = "00:00"
    urgency: str = "routine"


class InjectUpdateRequest(BaseModel):
    inject_type: str | None = None
    target_groups: list[str] | None = None
    content: str | None = None
    scheduled_offset: str | None = None
    urgency: str | None = None
    status: str | None = None


class InjectScoreRequest(BaseModel):
    score: float  # 0.0 to 1.0
    assessment_notes: str = ""


class ExerciseAssessRequest(BaseModel):
    pass  # no body needed, computes from inject scores


# --- Endpoints ---


@router.post("/exercise")
async def create_exercise(req: ExerciseCreateRequest):
    now = _now()
    exercise_id = _new_id()
    conn = get_db()
    try:
        conn.execute(
            "INSERT INTO exercises (id, name, status, created_at) VALUES (?, ?, 'planning', ?)",
            (exercise_id, req.name, now),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM exercises WHERE id = ?", (exercise_id,)).fetchone()
    finally:
        conn.close()
    log_activity("exercise_created", f"Exercise created: {req.name}", related_id=exercise_id)
    await notify_monitoring("exercise_created", f"Exercise created: {req.name}")
    return row_to_exercise(row)


@router.get("/exercise")
async def list_exercises():
    conn = get_db()
    try:
        rows = conn.execute("SELECT * FROM exercises ORDER BY created_at DESC").fetchall()
    finally:
        conn.close()
    return [row_to_exercise(r) for r in rows]


@router.get("/exercise/{exercise_id}")
async def get_exercise(exercise_id: str):
    conn = get_db()
    try:
        row = conn.execute("SELECT * FROM exercises WHERE id = ?", (exercise_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Exercise not found")
        inject_rows = conn.execute(
            "SELECT * FROM injects WHERE exercise_id = ? ORDER BY scheduled_offset", (exercise_id,)
        ).fetchall()
    finally:
        conn.close()
    result = row_to_exercise(row)
    result["injects"] = [row_to_inject(r) for r in inject_rows]
    return result


@router.put("/exercise/{exercise_id}")
async def update_exercise(exercise_id: str, req: ExerciseUpdateRequest):
    conn = get_db()
    try:
        existing = conn.execute("SELECT * FROM exercises WHERE id = ?", (exercise_id,)).fetchone()
        if not existing:
            raise HTTPException(status_code=404, detail="Exercise not found")
        conn.execute("UPDATE exercises SET status = ? WHERE id = ?", (req.status, exercise_id))
        conn.commit()
        row = conn.execute("SELECT * FROM exercises WHERE id = ?", (exercise_id,)).fetchone()
    finally:
        conn.close()
    severity = "warning" if req.status == "paused" else "info"
    log_activity("exercise_status_changed", f"Exercise '{existing['name']}' status: {existing['status']} -> {req.status}", severity=severity, related_id=exercise_id)
    await notify_monitoring("exercise_updated", f"Exercise status changed: {req.status}")
    return row_to_exercise(row)


@router.post("/exercise/{exercise_id}/inject")
async def create_inject(exercise_id: str, req: InjectCreateRequest):
    conn = get_db()
    try:
        ex = conn.execute("SELECT * FROM exercises WHERE id = ?", (exercise_id,)).fetchone()
        if not ex:
            raise HTTPException(status_code=404, detail="Exercise not found")
        now = _now()
        inject_id = _new_id()
        conn.execute(
            "INSERT INTO injects (id, exercise_id, inject_type, target_groups, content, scheduled_offset, urgency, status, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?)",
            (inject_id, exercise_id, req.inject_type, json.dumps(req.target_groups), req.content,
             req.scheduled_offset, req.urgency, now),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM injects WHERE id = ?", (inject_id,)).fetchone()
    finally:
        conn.close()
    log_activity("inject_created", f"Inject created in exercise {exercise_id}", related_id=inject_id)
    await notify_monitoring("inject_created", f"Inject created in exercise {exercise_id}")
    return row_to_inject(row)


@router.get("/exercise/{exercise_id}/injects")
async def list_injects(exercise_id: str):
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT * FROM injects WHERE exercise_id = ? ORDER BY scheduled_offset", (exercise_id,)
        ).fetchall()
    finally:
        conn.close()
    return [row_to_inject(r) for r in rows]


@router.put("/exercise/{exercise_id}/inject/{inject_id}")
async def update_inject(exercise_id: str, inject_id: str, req: InjectUpdateRequest):
    conn = get_db()
    try:
        existing = conn.execute(
            "SELECT * FROM injects WHERE id = ? AND exercise_id = ?", (inject_id, exercise_id)
        ).fetchone()
        if not existing:
            raise HTTPException(status_code=404, detail="Inject not found")
        updates = {}
        for field in ("inject_type", "content", "scheduled_offset", "urgency", "status"):
            val = getattr(req, field, None)
            if val is not None:
                updates[field] = val
        for field in ("target_groups",):
            val = getattr(req, field, None)
            if val is not None:
                updates[field] = json.dumps(val)
        if not updates:
            return row_to_inject(existing)
        set_clause = ", ".join(f"{k} = ?" for k in updates)
        conn.execute(f"UPDATE injects SET {set_clause} WHERE id = ? AND exercise_id = ?", (*updates.values(), inject_id, exercise_id))
        conn.commit()
        row = conn.execute("SELECT * FROM injects WHERE id = ?", (inject_id,)).fetchone()
    finally:
        conn.close()
    return row_to_inject(row)


@router.delete("/exercise/{exercise_id}/inject/{inject_id}")
async def delete_inject(exercise_id: str, inject_id: str):
    conn = get_db()
    try:
        existing = conn.execute(
            "SELECT * FROM injects WHERE id = ? AND exercise_id = ?", (inject_id, exercise_id)
        ).fetchone()
        if not existing:
            raise HTTPException(status_code=404, detail="Inject not found")
        conn.execute("DELETE FROM injects WHERE id = ? AND exercise_id = ?", (inject_id, exercise_id))
        conn.commit()
    finally:
        conn.close()
    log_activity("inject_deleted", f"Inject deleted from exercise {exercise_id}", related_id=inject_id)
    return {"detail": "deleted"}


@router.post("/exercise/{exercise_id}/inject/{inject_id}/deliver")
async def deliver_inject(exercise_id: str, inject_id: str):
    conn = get_db()
    try:
        existing = conn.execute(
            "SELECT * FROM injects WHERE id = ? AND exercise_id = ?", (inject_id, exercise_id)
        ).fetchone()
        if not existing:
            raise HTTPException(status_code=404, detail="Inject not found")
        conn.execute(
            "UPDATE injects SET status = 'delivered' WHERE id = ? AND exercise_id = ?",
            (inject_id, exercise_id),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM injects WHERE id = ?", (inject_id,)).fetchone()
    finally:
        conn.close()
    log_activity(
        "inject_delivered",
        f"Inject '{existing['inject_type']}' delivered to {existing['target_groups']}",
        severity="warning",
        related_id=inject_id,
    )
    return row_to_inject(row)


@router.post("/exercise/{exercise_id}/inject/{inject_id}/score")
async def score_inject(exercise_id: str, inject_id: str, req: InjectScoreRequest):
    conn = get_db()
    try:
        existing = conn.execute(
            "SELECT * FROM injects WHERE id = ? AND exercise_id = ?", (inject_id, exercise_id)
        ).fetchone()
        if not existing:
            raise HTTPException(status_code=404, detail="Inject not found")
        conn.execute(
            "UPDATE injects SET score = ?, assessment_notes = ? WHERE id = ? AND exercise_id = ?",
            (req.score, req.assessment_notes, inject_id, exercise_id),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM injects WHERE id = ?", (inject_id,)).fetchone()
    finally:
        conn.close()
    log_activity(
        "inject_scored",
        f"Inject '{existing['inject_type']}' scored {req.score:.2f}",
        related_id=inject_id,
    )
    return row_to_inject(row)


@router.post("/exercise/{exercise_id}/assess")
async def assess_exercise(exercise_id: str):
    conn = get_db()
    try:
        ex = conn.execute("SELECT * FROM exercises WHERE id = ?", (exercise_id,)).fetchone()
        if not ex:
            raise HTTPException(status_code=404, detail="Exercise not found")
        inject_rows = conn.execute(
            "SELECT * FROM injects WHERE exercise_id = ? ORDER BY scheduled_offset", (exercise_id,)
        ).fetchall()
    finally:
        conn.close()

    # Compute overall score (average of scored injects)
    scored = [r for r in inject_rows if r["score"] is not None]
    overall_score = sum(r["score"] for r in scored) / len(scored) if scored else 0.0

    # Generate assessment summary via LLM
    inject_summaries = []
    for r in inject_rows:
        score_str = f"{r['score']:.2f}" if r["score"] is not None else "unscored"
        notes = r["assessment_notes"] or "no notes"
        inject_summaries.append(
            f"- {r['inject_type']} (T+{r['scheduled_offset']}, status={r['status']}, score={score_str}): {r['content'][:120]}... Notes: {notes}"
        )
    inject_block = "\n".join(inject_summaries) if inject_summaries else "No injects."

    prompt = (
        f"You are an exercise assessment officer. The exercise '{ex['name']}' has completed. "
        f"Overall score: {overall_score:.2f} (average of {len(scored)} scored injects out of {len(inject_rows)} total). "
        f"Inject details:\n{inject_block}\n\n"
        f"Write a concise 3-5 sentence narrative assessment summary covering participant performance, "
        f"key observations, and recommendations for improvement. Be specific and professional."
    )

    assessment_summary = ""
    client = get_anthropic_client()
    if client:
        try:
            response = await asyncio.wait_for(
                client.messages.create(
                    model=config.model,
                    max_tokens=600,
                    messages=[{"role": "user", "content": prompt}],
                ),
                timeout=30,
            )
            assessment_summary = response.content[0].text.strip()
        except Exception as e:
            logger.warning("Assessment LLM call failed: %s", e)
            assessment_summary = f"Overall score: {overall_score:.2f}. {len(scored)} of {len(inject_rows)} injects scored. Automated narrative unavailable."
    else:
        assessment_summary = f"Overall score: {overall_score:.2f}. {len(scored)} of {len(inject_rows)} injects scored. LLM not configured for narrative generation."

    # Update exercise record
    conn = get_db()
    try:
        conn.execute(
            "UPDATE exercises SET overall_score = ?, assessment_summary = ? WHERE id = ?",
            (overall_score, assessment_summary, exercise_id),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM exercises WHERE id = ?", (exercise_id,)).fetchone()
        inject_rows_updated = conn.execute(
            "SELECT * FROM injects WHERE exercise_id = ? ORDER BY scheduled_offset", (exercise_id,)
        ).fetchall()
    finally:
        conn.close()

    log_activity(
        "exercise_assessed",
        f"Exercise '{ex['name']}' assessed with overall score {overall_score:.2f}",
        severity="info",
        related_id=exercise_id,
    )

    result = row_to_exercise(row)
    result["injects"] = [row_to_inject(r) for r in inject_rows_updated]
    return result
