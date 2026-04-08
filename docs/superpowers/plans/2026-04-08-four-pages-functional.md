# Four Pages Functional Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make all 4 stub pages (COA Workspace, Monitoring, Briefings, Exercise Control) functional with SQLite persistence, LLM-powered generation, drag-and-drop Kanban, and a real Leaflet monitoring map.

**Architecture:** New `src/db.py` handles all SQLite schema and helpers. New endpoints added to `src/api.py` in clearly marked sections. Frontend pages refactored from stubs to data-driven components. All mutations log to `activity_log` table for the monitoring dashboard.

**Tech Stack:** Python sqlite3, FastAPI, Anthropic Claude API, React 18, TypeScript, Tailwind v4, @dnd-kit (drag-and-drop), Leaflet, marked (markdown rendering)

---

## Task 1: SQLite Foundation (`src/db.py`)

**Files:**
- Create: `src/db.py`
- Modify: `src/api.py` (startup hook, line ~183)

- [ ] **Step 1: Create `src/db.py` with schema and helpers**

```python
"""SQLite database layer for Emissary platform state."""
from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = Path(__file__).parent.parent / "data" / "emissary.db"

def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

def _new_id() -> str:
    return uuid.uuid4().hex[:12]

def init_db() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS coas (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            description TEXT DEFAULT '',
            target_entities TEXT DEFAULT '[]',
            action_type TEXT DEFAULT '',
            status TEXT NOT NULL DEFAULT 'draft',
            confidence REAL,
            source_analysis_id TEXT,
            recommendations TEXT DEFAULT '[]',
            friendly_fire TEXT DEFAULT '[]',
            expected_effects TEXT DEFAULT '[]',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS activity_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            event_type TEXT NOT NULL,
            source TEXT DEFAULT 'system',
            message TEXT NOT NULL,
            severity TEXT DEFAULT 'info',
            related_id TEXT
        );
        CREATE TABLE IF NOT EXISTS briefings (
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            type TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'draft',
            reference_id TEXT,
            content_markdown TEXT DEFAULT '',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS exercises (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'planning',
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS injects (
            id TEXT PRIMARY KEY,
            exercise_id TEXT NOT NULL,
            inject_type TEXT NOT NULL,
            target_groups TEXT DEFAULT '[]',
            content TEXT DEFAULT '',
            scheduled_offset TEXT DEFAULT '00:00',
            urgency TEXT DEFAULT 'routine',
            status TEXT NOT NULL DEFAULT 'pending',
            created_at TEXT NOT NULL,
            FOREIGN KEY (exercise_id) REFERENCES exercises(id)
        );
    """)
    conn.close()

def get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

def log_activity(
    event_type: str,
    message: str,
    source: str = "system",
    severity: str = "info",
    related_id: str | None = None,
) -> None:
    conn = get_db()
    try:
        conn.execute(
            "INSERT INTO activity_log (timestamp, event_type, source, message, severity, related_id) VALUES (?, ?, ?, ?, ?, ?)",
            (_now(), event_type, source, message, severity, related_id),
        )
        conn.commit()
    finally:
        conn.close()

# --- Row converters (handle JSON deserialization) ---

_JSON_FIELDS_COA = {"target_entities", "recommendations", "friendly_fire", "expected_effects"}
_JSON_FIELDS_INJECT = {"target_groups"}

def _row_to_dict(row: sqlite3.Row, json_fields: set[str] | None = None) -> dict:
    d = dict(row)
    if json_fields:
        for f in json_fields:
            if f in d and isinstance(d[f], str):
                try:
                    d[f] = json.loads(d[f])
                except (json.JSONDecodeError, TypeError):
                    d[f] = []
    return d

def row_to_coa(row: sqlite3.Row) -> dict:
    return _row_to_dict(row, _JSON_FIELDS_COA)

def row_to_inject(row: sqlite3.Row) -> dict:
    return _row_to_dict(row, _JSON_FIELDS_INJECT)

def row_to_activity(row: sqlite3.Row) -> dict:
    return _row_to_dict(row)

def row_to_briefing(row: sqlite3.Row) -> dict:
    return _row_to_dict(row)

def row_to_exercise(row: sqlite3.Row) -> dict:
    return _row_to_dict(row)
```

- [ ] **Step 2: Wire `init_db()` into FastAPI startup**

In `src/api.py`, add the import near the top imports (around line 18):

```python
from src.db import init_db
```

Then in the `_startup()` function (line 183), add `init_db()` after the ACLED refresh:

```python
@app.on_event("startup")
async def _startup() -> None:
    global _browser_opened
    await refresh_acled_token()
    init_db()
    if not _browser_opened:
        _browser_opened = True
        webbrowser.open("http://localhost:8000")
```

- [ ] **Step 3: Verify database creates on startup**

Run: `uv run python -c "from src.db import init_db; init_db(); print('OK')"`
Expected: prints `OK`, creates `data/emissary.db`

- [ ] **Step 4: Commit**

```
feat: add SQLite database layer for Emissary platform state
```

---

## Task 2: COA Backend Endpoints

**Files:**
- Modify: `src/api.py` (add endpoints before the catch-all route at line ~2499)
- Reference: `src/db.py` (helpers from Task 1)

- [ ] **Step 1: Add COA imports and request models to `src/api.py`**

Add to the imports section (near line 18):
```python
from src.db import (
    get_db, log_activity, _now, _new_id,
    row_to_coa, row_to_briefing, row_to_exercise, row_to_inject, row_to_activity,
)
```

Add request models after the existing model definitions (around line 240):
```python
# --- COA Workspace models ---

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
```

- [ ] **Step 2: Add COA CRUD endpoints**

Insert before the `# --- Static file catch-all ---` comment (line ~2499):

```python
# --- COA Workspace ---

@app.post("/api/coa")
async def create_coa(req: COACreateRequest):
    coa_id = _new_id()
    now = _now()
    conn = get_db()
    try:
        conn.execute(
            """INSERT INTO coas (id, name, description, target_entities, action_type,
               status, confidence, source_analysis_id, recommendations, friendly_fire,
               expected_effects, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, 'draft', ?, ?, ?, ?, ?, ?, ?)""",
            (coa_id, req.name, req.description, json.dumps(req.target_entities),
             req.action_type, req.confidence, req.source_analysis_id,
             json.dumps(req.recommendations), json.dumps(req.friendly_fire),
             json.dumps(req.expected_effects), now, now),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM coas WHERE id = ?", (coa_id,)).fetchone()
    finally:
        conn.close()
    log_activity("coa_created", f"COA '{req.name}' created", related_id=coa_id)
    return row_to_coa(row)

@app.get("/api/coa")
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

@app.get("/api/coa/{coa_id}")
async def get_coa(coa_id: str):
    conn = get_db()
    try:
        row = conn.execute("SELECT * FROM coas WHERE id = ?", (coa_id,)).fetchone()
    finally:
        conn.close()
    if not row:
        raise HTTPException(status_code=404, detail="COA not found")
    return row_to_coa(row)

@app.put("/api/coa/{coa_id}")
async def update_coa(coa_id: str, req: COAUpdateRequest):
    conn = get_db()
    try:
        existing = conn.execute("SELECT * FROM coas WHERE id = ?", (coa_id,)).fetchone()
        if not existing:
            raise HTTPException(status_code=404, detail="COA not found")
        updates = {}
        for field in ["name", "description", "action_type", "status", "confidence"]:
            val = getattr(req, field, None)
            if val is not None:
                updates[field] = val
        for field in ["target_entities", "recommendations", "friendly_fire", "expected_effects"]:
            val = getattr(req, field, None)
            if val is not None:
                updates[field] = json.dumps(val)
        if updates:
            updates["updated_at"] = _now()
            set_clause = ", ".join(f"{k} = ?" for k in updates)
            conn.execute(f"UPDATE coas SET {set_clause} WHERE id = ?", (*updates.values(), coa_id))
            conn.commit()
        row = conn.execute("SELECT * FROM coas WHERE id = ?", (coa_id,)).fetchone()
    finally:
        conn.close()
    if req.status:
        log_activity("coa_status_changed", f"COA '{row_to_coa(row)['name']}' moved to {req.status}", related_id=coa_id)
    return row_to_coa(row)

@app.delete("/api/coa/{coa_id}")
async def delete_coa(coa_id: str):
    conn = get_db()
    try:
        row = conn.execute("SELECT name FROM coas WHERE id = ?", (coa_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="COA not found")
        conn.execute("DELETE FROM coas WHERE id = ?", (coa_id,))
        conn.commit()
    finally:
        conn.close()
    log_activity("coa_deleted", f"COA '{row['name']}' deleted", related_id=coa_id)
    return {"status": "deleted"}
```

- [ ] **Step 3: Add COA generate endpoint**

```python
_COA_GENERATE_SYSTEM = """You are a US national security and economic warfare strategist. Given intelligence data and a strategic objective, generate 2-3 distinct Courses of Action (COAs).

Return a JSON array where each element has:
- name: short COA title (5-8 words)
- description: 2-3 sentence summary
- action_type: one of [sanction, export_control, investment_screening, competitive_investment, regulatory_action, diplomatic_engagement]
- target_entities: array of entity names targeted
- expected_effects: array of expected outcomes (1-2 sentences each)
- friendly_fire: array of objects with {entity, details, risk_level} for US/allied exposure
- confidence: float 0-1

Return ONLY the JSON array, no markdown fences."""

@app.post("/api/coa/generate")
async def generate_coa_options(req: COAGenerateRequest):
    client = _get_anthropic_client()
    if not client:
        raise HTTPException(status_code=503, detail="Anthropic API not configured")
    source_data = req.analysis_data or {}
    prompt = f"STRATEGIC OBJECTIVE: {req.objective}\n\nINTELLIGENCE DATA:\n{json.dumps(source_data, default=str)[:8000]}"
    try:
        response = await asyncio.wait_for(
            client.messages.create(
                model=config.model,
                max_tokens=2000,
                system=_COA_GENERATE_SYSTEM,
                messages=[{"role": "user", "content": prompt}],
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
        raise HTTPException(status_code=500, detail=f"COA generation failed: {exc}")
```

- [ ] **Step 4: Verify COA endpoints work**

Run the server and test:
```bash
curl -X POST http://localhost:8000/api/coa -H "Content-Type: application/json" -d '{"name":"Test COA","action_type":"sanction"}'
curl http://localhost:8000/api/coa
```
Expected: COA is created and listed.

- [ ] **Step 5: Commit**

```
feat: add COA CRUD + LLM generation endpoints
```

---

## Task 3: Monitoring + Briefing + Exercise Backend Endpoints

**Files:**
- Modify: `src/api.py`

- [ ] **Step 1: Add remaining request models**

Add after the COA models:

```python
# --- Briefing models ---

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

# --- Exercise models ---

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
```

- [ ] **Step 2: Add Monitoring endpoints**

Insert after the COA endpoints:

```python
# --- Monitoring ---

_MONITORING_ZONES = [
    {"lat": 14.5, "lon": 114.0, "label": "South China Sea", "type": "monitoring_zone", "status": "active"},
    {"lat": 25.0, "lon": 121.5, "label": "Taiwan Strait", "type": "monitoring_zone", "status": "active"},
    {"lat": 2.0, "lon": 103.0, "label": "Strait of Malacca", "type": "monitoring_zone", "status": "active"},
    {"lat": 35.0, "lon": 129.0, "label": "Korean Peninsula", "type": "monitoring_zone", "status": "monitoring"},
    {"lat": -6.0, "lon": 106.0, "label": "Sunda Strait", "type": "monitoring_zone", "status": "monitoring"},
]

@app.get("/api/monitoring/kpis")
async def get_monitoring_kpis():
    conn = get_db()
    try:
        active_coas = conn.execute("SELECT COUNT(*) FROM coas WHERE status IN ('approved','executing')").fetchone()[0]
        total_coas = conn.execute("SELECT COUNT(*) FROM coas").fetchone()[0]
        total_activity = conn.execute("SELECT COUNT(*) FROM activity_log").fetchone()[0]
        active_injects = conn.execute("SELECT COUNT(*) FROM injects WHERE status = 'delivered'").fetchone()[0]
        last_row = conn.execute("SELECT timestamp FROM activity_log ORDER BY id DESC LIMIT 1").fetchone()
        last_event = last_row["timestamp"] if last_row else None
        total_briefings = conn.execute("SELECT COUNT(*) FROM briefings").fetchone()[0]
    finally:
        conn.close()
    return {
        "active_coas": active_coas,
        "total_coas": total_coas,
        "total_activity": total_activity,
        "active_injects": active_injects,
        "last_event": last_event,
        "total_briefings": total_briefings,
    }

@app.get("/api/monitoring/activity")
async def get_monitoring_activity(limit: int = 50):
    conn = get_db()
    try:
        rows = conn.execute("SELECT * FROM activity_log ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    finally:
        conn.close()
    return [row_to_activity(r) for r in rows]

@app.get("/api/monitoring/map-data")
async def get_monitoring_map_data():
    markers = list(_MONITORING_ZONES)
    return markers
```

- [ ] **Step 3: Add Briefing endpoints**

```python
# --- Briefings ---

_BRIEFING_SYSTEM = """You are a military intelligence briefing officer. Generate a structured briefing document in Markdown format.

Structure:
# [Title]

## I. Situation
[2-3 paragraphs summarizing the current intelligence picture]

## II. Analysis
[2-3 paragraphs analyzing implications, risks, and opportunities]

## III. Recommendation
[Specific, actionable recommendations with legal/policy mechanisms]

## IV. Risk Assessment
[Friendly fire concerns, second-order effects, confidence levels]

---
*Classification: UNCLASSIFIED // EXERCISE ONLY*

Write with authority. Use specific data from the source material. Be concise but thorough."""

@app.post("/api/briefing")
async def create_briefing(req: BriefingCreateRequest):
    briefing_id = _new_id()
    now = _now()
    conn = get_db()
    try:
        conn.execute(
            "INSERT INTO briefings (id, title, type, status, reference_id, content_markdown, created_at, updated_at) VALUES (?, ?, ?, 'draft', ?, ?, ?, ?)",
            (briefing_id, req.title, req.type, req.reference_id, req.content_markdown, now, now),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM briefings WHERE id = ?", (briefing_id,)).fetchone()
    finally:
        conn.close()
    log_activity("briefing_created", f"Briefing '{req.title}' created", related_id=briefing_id)
    return row_to_briefing(row)

@app.post("/api/briefing/generate")
async def generate_briefing(req: BriefingGenerateRequest):
    client = _get_anthropic_client()
    if not client:
        raise HTTPException(status_code=503, detail="Anthropic API not configured")
    source_data = {}
    title = "Intelligence Briefing"
    if req.coa_id:
        conn = get_db()
        try:
            row = conn.execute("SELECT * FROM coas WHERE id = ?", (req.coa_id,)).fetchone()
        finally:
            conn.close()
        if not row:
            raise HTTPException(status_code=404, detail="COA not found")
        source_data = row_to_coa(row)
        title = f"COA Brief: {source_data['name']}"
    elif req.analysis_id and req.analysis_id in _analyses:
        source_data = _analyses[req.analysis_id].get("result", {})
        title = f"Analysis Brief: {req.analysis_id[:8]}"
    prompt = f"Generate a {req.briefing_type} briefing based on this intelligence data:\n\n{json.dumps(source_data, default=str)[:6000]}"
    try:
        response = await asyncio.wait_for(
            client.messages.create(
                model=config.model,
                max_tokens=2000,
                system=_BRIEFING_SYSTEM,
                messages=[{"role": "user", "content": prompt}],
            ),
            timeout=30.0,
        )
        content = response.content[0].text.strip()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Briefing generation failed: {exc}")
    briefing_id = _new_id()
    now = _now()
    conn = get_db()
    try:
        conn.execute(
            "INSERT INTO briefings (id, title, type, status, reference_id, content_markdown, created_at, updated_at) VALUES (?, ?, ?, 'draft', ?, ?, ?, ?)",
            (briefing_id, title, req.briefing_type, req.coa_id or req.analysis_id, content, now, now),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM briefings WHERE id = ?", (briefing_id,)).fetchone()
    finally:
        conn.close()
    log_activity("briefing_generated", f"Briefing '{title}' generated via LLM", related_id=briefing_id)
    return row_to_briefing(row)

@app.get("/api/briefing")
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

@app.get("/api/briefing/{briefing_id}")
async def get_briefing(briefing_id: str):
    conn = get_db()
    try:
        row = conn.execute("SELECT * FROM briefings WHERE id = ?", (briefing_id,)).fetchone()
    finally:
        conn.close()
    if not row:
        raise HTTPException(status_code=404, detail="Briefing not found")
    return row_to_briefing(row)

@app.put("/api/briefing/{briefing_id}")
async def update_briefing(briefing_id: str, req: BriefingUpdateRequest):
    conn = get_db()
    try:
        existing = conn.execute("SELECT * FROM briefings WHERE id = ?", (briefing_id,)).fetchone()
        if not existing:
            raise HTTPException(status_code=404, detail="Briefing not found")
        updates: dict[str, Any] = {}
        if req.status is not None:
            updates["status"] = req.status
        if req.title is not None:
            updates["title"] = req.title
        if req.content_markdown is not None:
            updates["content_markdown"] = req.content_markdown
        if updates:
            updates["updated_at"] = _now()
            set_clause = ", ".join(f"{k} = ?" for k in updates)
            conn.execute(f"UPDATE briefings SET {set_clause} WHERE id = ?", (*updates.values(), briefing_id))
            conn.commit()
        row = conn.execute("SELECT * FROM briefings WHERE id = ?", (briefing_id,)).fetchone()
    finally:
        conn.close()
    if req.status:
        log_activity("briefing_status_changed", f"Briefing status changed to {req.status}", related_id=briefing_id)
    return row_to_briefing(row)

@app.delete("/api/briefing/{briefing_id}")
async def delete_briefing(briefing_id: str):
    conn = get_db()
    try:
        row = conn.execute("SELECT title FROM briefings WHERE id = ?", (briefing_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Briefing not found")
        conn.execute("DELETE FROM briefings WHERE id = ?", (briefing_id,))
        conn.commit()
    finally:
        conn.close()
    log_activity("briefing_deleted", f"Briefing '{row['title']}' deleted", related_id=briefing_id)
    return {"status": "deleted"}
```

- [ ] **Step 4: Add Exercise Control endpoints**

```python
# --- Exercise Control ---

@app.post("/api/exercise")
async def create_exercise(req: ExerciseCreateRequest):
    exercise_id = _new_id()
    now = _now()
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
    log_activity("exercise_created", f"Exercise '{req.name}' created", related_id=exercise_id)
    return row_to_exercise(row)

@app.get("/api/exercise")
async def list_exercises():
    conn = get_db()
    try:
        rows = conn.execute("SELECT * FROM exercises ORDER BY created_at DESC").fetchall()
    finally:
        conn.close()
    return [row_to_exercise(r) for r in rows]

@app.get("/api/exercise/{exercise_id}")
async def get_exercise(exercise_id: str):
    conn = get_db()
    try:
        row = conn.execute("SELECT * FROM exercises WHERE id = ?", (exercise_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Exercise not found")
        inject_rows = conn.execute("SELECT * FROM injects WHERE exercise_id = ? ORDER BY scheduled_offset", (exercise_id,)).fetchall()
    finally:
        conn.close()
    result = row_to_exercise(row)
    result["injects"] = [row_to_inject(r) for r in inject_rows]
    return result

@app.put("/api/exercise/{exercise_id}")
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
    log_activity("exercise_status_changed", f"Exercise status changed to {req.status}", severity=severity, related_id=exercise_id)
    return row_to_exercise(row)

@app.post("/api/exercise/{exercise_id}/inject")
async def create_inject(exercise_id: str, req: InjectCreateRequest):
    conn = get_db()
    try:
        ex = conn.execute("SELECT id FROM exercises WHERE id = ?", (exercise_id,)).fetchone()
        if not ex:
            raise HTTPException(status_code=404, detail="Exercise not found")
        inject_id = _new_id()
        now = _now()
        conn.execute(
            "INSERT INTO injects (id, exercise_id, inject_type, target_groups, content, scheduled_offset, urgency, status, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?)",
            (inject_id, exercise_id, req.inject_type, json.dumps(req.target_groups), req.content, req.scheduled_offset, req.urgency, now),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM injects WHERE id = ?", (inject_id,)).fetchone()
    finally:
        conn.close()
    log_activity("inject_created", f"Inject '{req.inject_type}' added at T+{req.scheduled_offset}", related_id=inject_id)
    return row_to_inject(row)

@app.get("/api/exercise/{exercise_id}/injects")
async def list_injects(exercise_id: str):
    conn = get_db()
    try:
        rows = conn.execute("SELECT * FROM injects WHERE exercise_id = ? ORDER BY scheduled_offset", (exercise_id,)).fetchall()
    finally:
        conn.close()
    return [row_to_inject(r) for r in rows]

@app.put("/api/exercise/{exercise_id}/inject/{inject_id}")
async def update_inject(exercise_id: str, inject_id: str, req: InjectUpdateRequest):
    conn = get_db()
    try:
        existing = conn.execute("SELECT * FROM injects WHERE id = ? AND exercise_id = ?", (inject_id, exercise_id)).fetchone()
        if not existing:
            raise HTTPException(status_code=404, detail="Inject not found")
        updates: dict[str, Any] = {}
        if req.inject_type is not None:
            updates["inject_type"] = req.inject_type
        if req.target_groups is not None:
            updates["target_groups"] = json.dumps(req.target_groups)
        if req.content is not None:
            updates["content"] = req.content
        if req.scheduled_offset is not None:
            updates["scheduled_offset"] = req.scheduled_offset
        if req.urgency is not None:
            updates["urgency"] = req.urgency
        if req.status is not None:
            updates["status"] = req.status
        if updates:
            set_clause = ", ".join(f"{k} = ?" for k in updates)
            conn.execute(f"UPDATE injects SET {set_clause} WHERE id = ?", (*updates.values(), inject_id))
            conn.commit()
        row = conn.execute("SELECT * FROM injects WHERE id = ?", (inject_id,)).fetchone()
    finally:
        conn.close()
    return row_to_inject(row)

@app.delete("/api/exercise/{exercise_id}/inject/{inject_id}")
async def delete_inject(exercise_id: str, inject_id: str):
    conn = get_db()
    try:
        existing = conn.execute("SELECT * FROM injects WHERE id = ? AND exercise_id = ?", (inject_id, exercise_id)).fetchone()
        if not existing:
            raise HTTPException(status_code=404, detail="Inject not found")
        conn.execute("DELETE FROM injects WHERE id = ?", (inject_id,))
        conn.commit()
    finally:
        conn.close()
    log_activity("inject_deleted", "Inject deleted", related_id=inject_id)
    return {"status": "deleted"}
```

- [ ] **Step 5: Verify all endpoints work**

```bash
# Monitoring
curl http://localhost:8000/api/monitoring/kpis
curl http://localhost:8000/api/monitoring/activity
curl http://localhost:8000/api/monitoring/map-data

# Briefings
curl -X POST http://localhost:8000/api/briefing -H "Content-Type: application/json" -d '{"title":"Test Brief","type":"situation_update","content_markdown":"# Test\nContent here"}'
curl http://localhost:8000/api/briefing

# Exercises
curl -X POST http://localhost:8000/api/exercise -H "Content-Type: application/json" -d '{"name":"Global Sentinel 24"}'
```

- [ ] **Step 6: Commit**

```
feat: add monitoring, briefing, and exercise control backend endpoints
```

---

## Task 4: Frontend Types and API Client

**Files:**
- Modify: `frontend/src/types.ts`
- Modify: `frontend/src/api.ts`

- [ ] **Step 1: Add TypeScript interfaces to `types.ts`**

Append to the end of `frontend/src/types.ts`:

```typescript
// --- COA Workspace ---

export interface COA {
  id: string
  name: string
  description: string
  target_entities: string[]
  action_type: string
  status: 'draft' | 'under_review' | 'approved' | 'executing' | 'assessed'
  confidence: number | null
  source_analysis_id: string | null
  recommendations: string[]
  friendly_fire: Record<string, unknown>[]
  expected_effects: string[]
  created_at: string
  updated_at: string
}

// --- Monitoring ---

export interface KPIData {
  active_coas: number
  total_coas: number
  total_activity: number
  active_injects: number
  last_event: string | null
  total_briefings: number
}

export interface ActivityEntry {
  id: number
  timestamp: string
  event_type: string
  source: string
  message: string
  severity: string
  related_id: string | null
}

export interface MapMarker {
  lat: number
  lon: number
  label: string
  type: string
  status: string
}

// --- Briefings ---

export interface Briefing {
  id: string
  title: string
  type: 'coa_brief' | 'bda_report' | 'situation_update' | 'exercise_summary'
  status: 'draft' | 'reviewing' | 'finalized'
  reference_id: string | null
  content_markdown: string
  created_at: string
  updated_at: string
}

// --- Exercise Control ---

export interface Exercise {
  id: string
  name: string
  status: 'planning' | 'active' | 'paused' | 'completed'
  created_at: string
  injects?: Inject[]
}

export interface Inject {
  id: string
  exercise_id: string
  inject_type: string
  target_groups: string[]
  content: string
  scheduled_offset: string
  urgency: string
  status: 'pending' | 'delivered' | 'acknowledged'
  created_at: string
}
```

- [ ] **Step 2: Add API client functions to `api.ts`**

Append to the end of `frontend/src/api.ts`:

```typescript
// --- COA Workspace ---

import type { COA, KPIData, ActivityEntry, MapMarker, Briefing, Exercise, Inject } from './types'

export async function fetchCOAs(status?: string): Promise<COA[]> {
  const params = status ? `?status=${status}` : ''
  const res = await fetch(`${API_BASE}/api/coa${params}`)
  return parseJson<COA[]>(res)
}

export async function fetchCOA(id: string): Promise<COA> {
  const res = await fetch(`${API_BASE}/api/coa/${id}`)
  return parseJson<COA>(res)
}

export async function createCOA(data: Partial<COA>): Promise<COA> {
  const res = await fetch(`${API_BASE}/api/coa`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  })
  return parseJson<COA>(res)
}

export async function updateCOA(id: string, data: Partial<COA>): Promise<COA> {
  const res = await fetch(`${API_BASE}/api/coa/${id}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  })
  return parseJson<COA>(res)
}

export async function deleteCOA(id: string): Promise<void> {
  const res = await fetch(`${API_BASE}/api/coa/${id}`, { method: 'DELETE' })
  await parseJson<{ status: string }>(res)
}

export async function generateCOAOptions(params: {
  analysis_data?: Record<string, unknown>
  objective: string
}): Promise<Partial<COA>[]> {
  const res = await fetch(`${API_BASE}/api/coa/generate`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(params),
  })
  return parseJson<Partial<COA>[]>(res)
}

// --- Monitoring ---

export async function fetchKPIs(): Promise<KPIData> {
  const res = await fetch(`${API_BASE}/api/monitoring/kpis`)
  return parseJson<KPIData>(res)
}

export async function fetchActivity(limit = 50): Promise<ActivityEntry[]> {
  const res = await fetch(`${API_BASE}/api/monitoring/activity?limit=${limit}`)
  return parseJson<ActivityEntry[]>(res)
}

export async function fetchMapData(): Promise<MapMarker[]> {
  const res = await fetch(`${API_BASE}/api/monitoring/map-data`)
  return parseJson<MapMarker[]>(res)
}

// --- Briefings ---

export async function fetchBriefings(): Promise<Briefing[]> {
  const res = await fetch(`${API_BASE}/api/briefing`)
  return parseJson<Briefing[]>(res)
}

export async function fetchBriefing(id: string): Promise<Briefing> {
  const res = await fetch(`${API_BASE}/api/briefing/${id}`)
  return parseJson<Briefing>(res)
}

export async function createBriefing(data: Partial<Briefing>): Promise<Briefing> {
  const res = await fetch(`${API_BASE}/api/briefing`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  })
  return parseJson<Briefing>(res)
}

export async function generateBriefing(params: {
  coa_id?: string
  analysis_id?: string
  briefing_type?: string
}): Promise<Briefing> {
  const res = await fetch(`${API_BASE}/api/briefing/generate`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(params),
  })
  return parseJson<Briefing>(res)
}

export async function updateBriefing(id: string, data: Partial<Briefing>): Promise<Briefing> {
  const res = await fetch(`${API_BASE}/api/briefing/${id}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  })
  return parseJson<Briefing>(res)
}

export async function deleteBriefing(id: string): Promise<void> {
  const res = await fetch(`${API_BASE}/api/briefing/${id}`, { method: 'DELETE' })
  await parseJson<{ status: string }>(res)
}

// --- Exercise Control ---

export async function fetchExercises(): Promise<Exercise[]> {
  const res = await fetch(`${API_BASE}/api/exercise`)
  return parseJson<Exercise[]>(res)
}

export async function fetchExercise(id: string): Promise<Exercise> {
  const res = await fetch(`${API_BASE}/api/exercise/${id}`)
  return parseJson<Exercise>(res)
}

export async function createExercise(name: string): Promise<Exercise> {
  const res = await fetch(`${API_BASE}/api/exercise`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name }),
  })
  return parseJson<Exercise>(res)
}

export async function updateExercise(id: string, status: string): Promise<Exercise> {
  const res = await fetch(`${API_BASE}/api/exercise/${id}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ status }),
  })
  return parseJson<Exercise>(res)
}

export async function createInject(exerciseId: string, data: Partial<Inject>): Promise<Inject> {
  const res = await fetch(`${API_BASE}/api/exercise/${exerciseId}/inject`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  })
  return parseJson<Inject>(res)
}

export async function fetchInjects(exerciseId: string): Promise<Inject[]> {
  const res = await fetch(`${API_BASE}/api/exercise/${exerciseId}/injects`)
  return parseJson<Inject[]>(res)
}

export async function updateInject(exerciseId: string, injectId: string, data: Partial<Inject>): Promise<Inject> {
  const res = await fetch(`${API_BASE}/api/exercise/${exerciseId}/inject/${injectId}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  })
  return parseJson<Inject>(res)
}

export async function deleteInject(exerciseId: string, injectId: string): Promise<void> {
  const res = await fetch(`${API_BASE}/api/exercise/${exerciseId}/inject/${injectId}`, { method: 'DELETE' })
  await parseJson<{ status: string }>(res)
}
```

- [ ] **Step 3: Verify TypeScript compiles**

Run: `cd frontend && npx tsc --noEmit`
Expected: no errors

- [ ] **Step 4: Commit**

```
feat: add frontend types and API client for COA, monitoring, briefings, exercises
```

---

## Task 5: Install @dnd-kit and Build COA Workspace Frontend

**Files:**
- Modify: `frontend/package.json` (install deps)
- Rewrite: `frontend/src/pages/COAWorkspacePage.tsx`

- [ ] **Step 1: Install @dnd-kit**

```bash
cd frontend && npm install @dnd-kit/core @dnd-kit/sortable @dnd-kit/utilities
```

- [ ] **Step 2: Rewrite COAWorkspacePage.tsx**

This is a full rewrite of the stub — the complete functional component with drag-and-drop Kanban, detail panel, and COA generation modal. Due to size, this task creates the complete file. The agent implementing this task should write the full COAWorkspacePage.tsx with:

- State: `coas`, `selectedCoa`, `loading`, `showGenerateModal`, `generating`, `generateObjective`, `generatedOptions`, `showCreateModal`, `createForm`
- `useEffect` on mount to `fetchCOAs()` 
- 5 Kanban columns using `@dnd-kit/core` `DndContext` with `DragOverlay`
- Each column is a droppable container with `useDroppable`
- Each card is draggable with `useDraggable`
- On `onDragEnd`: if card moves to new column, call `updateCOA(id, { status: newStatus })`
- Cards show: name, action_type badge, confidence level
- Click card sets `selectedCoa` for the right detail panel
- Detail panel shows: description, recommendations list, friendly_fire entries, expected_effects, action buttons (delete, status transitions)
- "Generate COA Options" button opens modal with objective textarea, calls `generateCOAOptions()`, shows results as accept/reject cards
- "Create COA" form modal with name, description, action_type, target_entities fields
- Use Tailwind classes matching the existing stub styling (bg-surface-container, text-on-surface, etc.)
- Column status mapping: `{ 'Draft': 'draft', 'Under Review': 'under_review', 'Approved': 'approved', 'Executing': 'executing', 'Assessed': 'assessed' }`

- [ ] **Step 3: Verify build**

```bash
cd frontend && npx vite build 2>&1 | tail -5
```
Expected: build succeeds

- [ ] **Step 4: Commit**

```
feat: implement functional COA Workspace with drag-and-drop Kanban
```

---

## Task 6: Monitoring Map Component + Page Refactor

**Files:**
- Create: `frontend/src/components/MonitoringMap.tsx`
- Rewrite: `frontend/src/pages/MonitoringPage.tsx`

- [ ] **Step 1: Create MonitoringMap.tsx**

Model on existing `AISRouteMap.tsx` pattern (raw Leaflet with useRef):

```typescript
import { useEffect, useRef } from 'react'
import L from 'leaflet'
import 'leaflet/dist/leaflet.css'
import type { MapMarker } from '../types'

interface Props {
  markers: MapMarker[]
}

const MARKER_COLORS: Record<string, string> = {
  coa_target: '#58a6ff',
  monitoring_zone: '#f1c04c',
  vessel: '#f85149',
}

export default function MonitoringMap({ markers }: Props) {
  const mapRef = useRef<HTMLDivElement>(null)
  const mapInstance = useRef<L.Map | null>(null)

  useEffect(() => {
    if (!mapRef.current) return
    if (mapInstance.current) {
      mapInstance.current.remove()
      mapInstance.current = null
    }
    const map = L.map(mapRef.current, { zoomControl: true })
    mapInstance.current = map
    L.tileLayer('https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png', {
      attribution: '&copy; OpenStreetMap &copy; CARTO',
      maxZoom: 19,
    }).addTo(map)
    map.setView([10, 115], 4)

    for (const m of markers) {
      const color = MARKER_COLORS[m.type] || '#8b919d'
      L.circleMarker([m.lat, m.lon], {
        radius: m.type === 'monitoring_zone' ? 8 : 6,
        color,
        fillColor: color,
        fillOpacity: 0.6,
        weight: 2,
      })
        .bindTooltip(`<strong>${m.label}</strong><br/>${m.type} · ${m.status}`, { className: 'leaflet-tooltip' })
        .addTo(map)
    }

    return () => {
      map.remove()
      mapInstance.current = null
    }
  }, [markers])

  return (
    <div
      ref={mapRef}
      className="w-full h-full rounded-lg"
      style={{ minHeight: '300px' }}
    />
  )
}
```

- [ ] **Step 2: Rewrite MonitoringPage.tsx**

Full rewrite with polling KPIs, activity log, and real map. The agent should write the complete MonitoringPage.tsx with:

- State: `kpis`, `activity`, `markers`
- `useEffect` on mount: fetch KPIs, activity, map data
- `useEffect` with `setInterval` every 30s to poll KPIs and activity (cleanup interval on unmount)
- KPI cards: replace `--` with actual values from `kpis` state
- Map area: replace placeholder with `<MonitoringMap markers={markers} />`
- Keep glass-panel HUD overlays on top of map
- Activity log: map `activity` entries to styled log items with severity-based icons
- Financial indicators: keep as styled placeholders

- [ ] **Step 3: Verify build**

```bash
cd frontend && npx vite build 2>&1 | tail -5
```

- [ ] **Step 4: Commit**

```
feat: implement Monitoring page with Leaflet map and live KPIs
```

---

## Task 7: Briefings Page Refactor

**Files:**
- Rewrite: `frontend/src/pages/BriefingsPage.tsx`

- [ ] **Step 1: Rewrite BriefingsPage.tsx**

Full rewrite with briefing list, document preview, and generation modal. The agent should write the complete BriefingsPage.tsx with:

- State: `briefings`, `selectedBriefing`, `showNewModal`, `generating`, `newBriefForm`
- `useEffect` on mount: `fetchBriefings()`
- Table populated from `briefings` array. Columns: Title, Type (badge), Status (badge with color), Actions (download icon)
- Click row sets `selectedBriefing`
- Document preview card: when `selectedBriefing` is set, render:
  - Header: "E" logo, document number = briefing.id, date = briefing.created_at
  - Title: `selectedBriefing.title`
  - Body: render `content_markdown` using `marked` (already installed). Use `dangerouslySetInnerHTML`. Style the white-on-dark document card to have readable dark text.
  - Footer: classification banner
- "APPROVE BRIEF" button: calls `updateBriefing(id, { status: nextStatus })` where draft→reviewing→finalized
- "NEW BRIEF" button opens modal with: title input, type dropdown (coa_brief, bda_report, situation_update, exercise_summary), "Generate" button that calls `generateBriefing()`
- Analytics grid at bottom: compute counts from briefings array
- Type badge colors: coa_brief=primary, bda_report=tertiary, situation_update=secondary, exercise_summary=outline
- Status badge colors: draft=outline, reviewing=tertiary, finalized=secondary

- [ ] **Step 2: Verify build**

```bash
cd frontend && npx vite build 2>&1 | tail -5
```

- [ ] **Step 3: Commit**

```
feat: implement Briefings page with document preview and LLM generation
```

---

## Task 8: Exercise Control Page Refactor

**Files:**
- Rewrite: `frontend/src/pages/ExerciseControlPage.tsx`

- [ ] **Step 1: Rewrite ExerciseControlPage.tsx**

Full rewrite with exercise management, inject builder, and timeline. The agent should write the complete ExerciseControlPage.tsx with:

- State: `exercises`, `activeExercise`, `injects`, `injectForm` (inject_type, target_groups, content, scheduled_offset, urgency), `showCreateExercise`, `newExerciseName`
- `useEffect` on mount: `fetchExercises()`, auto-select first exercise if exists
- When `activeExercise` changes: `fetchInjects(activeExercise.id)` to populate inject list
- Exercise selector: dropdown of exercises + "Create New" button
- Session ID: show `activeExercise?.id ?? '---'`
- "EMERGENCY PAUSE": enabled when `activeExercise?.status === 'active'`, calls `updateExercise(id, 'paused')`
- "DUMP LOGS": no-op for now (disabled)
- Inject timeline: plot injects along 24h bar. Parse `scheduled_offset` (HH:MM format) to calculate position as percentage of 24h. Color dots by status: pending=outline, delivered=secondary, acknowledged=primary. Show label on hover.
- Inject builder form: all fields wired to `injectForm` state, target_groups as toggle buttons (selected adds to array), "Commit Inject" calls `createInject(activeExercise.id, injectForm)` then refreshes inject list
- Status cards: "Active Units" = 142/150 (static mock), "Queries/Sec" = static mock, "Avg Decision Score" = static mock
- Participant table: 3-4 hardcoded mock rows for demo realism (INDOPACOM_J2, STATE_DPT_EAP, CYBER_COM_T5, JTF_HOA_CDR)
- System control log: fetch from `fetchActivity()` and display as mono log lines

- [ ] **Step 2: Verify build**

```bash
cd frontend && npx vite build 2>&1 | tail -5
```

- [ ] **Step 3: Commit**

```
feat: implement Exercise Control page with inject builder and timeline
```

---

## Task 9: Final Integration Verification

**Files:** None (verification only)

- [ ] **Step 1: Full build check**

```bash
cd frontend && npx tsc --noEmit && npx vite build 2>&1 | tail -10
```
Expected: no TypeScript errors, build succeeds

- [ ] **Step 2: End-to-end flow test**

Start the server and verify:
1. Navigate to COA Workspace → create a COA manually → see it in Draft column → drag to Under Review
2. Navigate to Monitoring → see KPI cards with at least 1 COA count → see map with Indo-Pacific markers → see activity log entry for COA creation
3. Navigate to Briefings → click "NEW BRIEF" → generate a briefing → see it in table → click to preview markdown → approve it
4. Navigate to Exercise Control → create an exercise → add an inject → see it on timeline

- [ ] **Step 3: Commit**

```
feat: complete 4-page functional implementation — COA, monitoring, briefings, exercise control
```
