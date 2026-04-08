# Design: Making COA, Monitoring, Briefings, and Exercise Control Functional

**Date:** 2026-04-08
**Status:** Draft

---

## Context

The Emissary frontend has 5 pages. Only Search works — the other 4 (COA Workspace, Monitoring, Briefings, Exercise Control) are stub layouts with placeholder content. This spec defines the minimal backend + frontend work to make all 4 pages functional with real data persistence, LLM-powered generation, and interactive UI.

## Decisions

- **Storage:** SQLite via Python `sqlite3` (persistent across restarts, no ORM)
- **No WebSockets:** Polling for real-time-ish data (30s intervals)
- **Monitoring map:** Real Leaflet map reusing existing library
- **COA status changes:** Drag-and-drop between Kanban columns (install `@dnd-kit/core` + `@dnd-kit/sortable`)
- **LLM generation:** Reuse existing Anthropic client for COA generation and briefing content

---

## Phase 0: SQLite Foundation

### New file: `src/db.py`

**Database:** `data/emissary.db`

**Tables:**

```sql
coas (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  description TEXT,
  target_entities TEXT,  -- JSON array
  action_type TEXT,
  status TEXT NOT NULL DEFAULT 'draft',  -- draft|under_review|approved|executing|assessed
  confidence REAL,
  source_analysis_id TEXT,
  recommendations TEXT,  -- JSON array
  friendly_fire TEXT,    -- JSON array
  expected_effects TEXT, -- JSON array
  created_at TEXT DEFAULT (datetime('now')),
  updated_at TEXT DEFAULT (datetime('now'))
)

activity_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  timestamp TEXT DEFAULT (datetime('now')),
  event_type TEXT NOT NULL,
  source TEXT,
  message TEXT NOT NULL,
  severity TEXT DEFAULT 'info',  -- info|warning|error|critical
  related_id TEXT
)

briefings (
  id TEXT PRIMARY KEY,
  title TEXT NOT NULL,
  type TEXT NOT NULL,    -- coa_brief|bda_report|situation_update|exercise_summary
  status TEXT DEFAULT 'draft',  -- draft|reviewing|finalized
  reference_id TEXT,
  content_markdown TEXT,
  created_at TEXT,
  updated_at TEXT
)

exercises (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  status TEXT DEFAULT 'planning',  -- planning|active|paused|completed
  created_at TEXT
)

injects (
  id TEXT PRIMARY KEY,
  exercise_id TEXT NOT NULL REFERENCES exercises(id),
  inject_type TEXT NOT NULL,
  target_groups TEXT,    -- JSON array
  content TEXT,
  scheduled_offset TEXT,
  urgency TEXT DEFAULT 'routine',
  status TEXT DEFAULT 'pending',  -- pending|delivered|acknowledged
  created_at TEXT
)
```

**Helper functions:**
- `init_db()` — creates all tables idempotently
- `get_db()` — returns connection with `row_factory = sqlite3.Row`
- `log_activity(event_type, message, source="system", severity="info", related_id=None)` — inserts into activity_log
- Row-to-dict converters for each table (handles JSON deserialization)

**Integration:** Call `init_db()` in FastAPI startup.

---

## Phase 1: COA Workspace

### Backend (add to `src/api.py`)

**Endpoints:**
| Method | Path | Purpose |
|--------|------|---------|
| POST | `/api/coa` | Create COA |
| GET | `/api/coa` | List all COAs (optional `?status=` filter) |
| GET | `/api/coa/{id}` | Get COA detail |
| PUT | `/api/coa/{id}` | Update COA (fields, status) |
| DELETE | `/api/coa/{id}` | Delete COA |
| POST | `/api/coa/generate` | LLM generates 2-3 COA options from analysis data + objective |

**`POST /api/coa/generate` detail:**
- Input: `{ analysis_id?: string, analysis_data?: dict, objective: string }`
- Builds prompt from analysis data + objective
- Uses Claude to generate structured COA objects (name, description, action_type, target_entities, expected_effects, friendly_fire, confidence)
- Returns array of COA options (not yet saved — user picks which to accept)
- Uses existing `_get_anthropic_client()` and `config.model`

### Frontend

**Types:** `COA` interface with all fields from the DB schema.

**API functions:** `fetchCOAs`, `fetchCOA`, `createCOA`, `updateCOA`, `deleteCOA`, `generateCOAOptions`

**COAWorkspacePage.tsx refactor:**
- Fetch COAs on mount, group by status into 5 Kanban columns
- **Drag-and-drop** using `@dnd-kit/core` + `@dnd-kit/sortable`:
  - Each column is a droppable area
  - Each card is a draggable item
  - On drop, call `updateCOA(id, { status: newColumnStatus })`
- Card shows: name, action_type badge, confidence indicator
- Click card → populates right detail panel with full COA data
- Detail panel shows: recommendations list, friendly fire alerts, expected effects, comparison matrix
- "Generate COA Options" button → modal with objective input → calls generate endpoint → shows results as accept/reject cards
- Manual "Create COA" form

---

## Phase 2: Monitoring

### Backend (add to `src/api.py`)

**Endpoints:**
| Method | Path | Purpose |
|--------|------|---------|
| GET | `/api/monitoring/kpis` | Aggregated counts from COA/activity/inject tables |
| GET | `/api/monitoring/activity` | Recent activity log entries (limit 50) |
| GET | `/api/monitoring/map-data` | Geo markers for map (COA targets + monitoring zones) |

**KPI queries:** Count active COAs, total activity entries, delivered injects, last event timestamp.

**Map data:** Returns array of `{ lat, lon, label, type, status }`. Includes hardcoded Indo-Pacific monitoring zones as baseline so map is never empty, plus any geo-referenced COA targets.

**Activity log integration:** All COA, briefing, and exercise endpoints call `log_activity()` on mutations.

### Frontend

**Types:** `KPIData`, `ActivityEntry`, `MapMarker` interfaces.

**New component: `MonitoringMap.tsx`**
- Leaflet map modeled on existing `AISRouteMap.tsx`
- Dark CARTO basemap, centered on Indo-Pacific `[10, 115]`, zoom 4
- Circle markers colored by type (blue=COA target, gold=monitoring zone, red=vessel)
- Tooltips on hover

**MonitoringPage.tsx refactor:**
- KPI cards populated from polling `/api/monitoring/kpis` every 30s
- Map component replaces placeholder
- Activity log populated from `/api/monitoring/activity`
- Financial indicators stay as styled placeholders

---

## Phase 3: Briefings

### Backend (add to `src/api.py`)

**Endpoints:**
| Method | Path | Purpose |
|--------|------|---------|
| POST | `/api/briefing` | Create briefing manually |
| POST | `/api/briefing/generate` | LLM generates briefing from COA or analysis |
| GET | `/api/briefing` | List all briefings |
| GET | `/api/briefing/{id}` | Get briefing with content |
| PUT | `/api/briefing/{id}` | Update status/content |
| DELETE | `/api/briefing/{id}` | Delete briefing |

**`POST /api/briefing/generate` detail:**
- Input: `{ coa_id?: string, analysis_id?: string, briefing_type: string }`
- Fetches source data (COA from SQLite or analysis from `_analyses` dict)
- Uses Claude with briefing-specific system prompt and `max_tokens=2000`
- Generates structured Markdown document (Situation, Analysis, Recommendation sections)
- Auto-creates briefing record in DB, returns it

### Frontend

**Types:** `Briefing` interface.

**BriefingsPage.tsx refactor:**
- Table populated from `fetchBriefings()`
- Rows show: title, type badge, status badge (Draft/Reviewing/Finalized with color coding)
- Click row → document preview card renders `content_markdown` via `marked` library
- "NEW BRIEF" button → modal with title, type dropdown, optional source (COA/analysis), generate button
- "APPROVE BRIEF" button transitions status (draft→reviewing→finalized)
- Analytics grid: compute counts from briefings array (pending approvals, finalized count)

---

## Phase 4: Exercise Control

### Backend (add to `src/api.py`)

**Endpoints:**
| Method | Path | Purpose |
|--------|------|---------|
| POST | `/api/exercise` | Create exercise |
| GET | `/api/exercise` | List exercises |
| GET | `/api/exercise/{id}` | Get exercise with injects |
| PUT | `/api/exercise/{id}` | Update status (start/pause/complete) |
| POST | `/api/exercise/{id}/inject` | Add inject |
| GET | `/api/exercise/{id}/injects` | List injects |
| PUT | `/api/exercise/{id}/inject/{iid}` | Update inject |
| DELETE | `/api/exercise/{id}/inject/{iid}` | Delete inject |

### Frontend

**Types:** `Exercise`, `Inject` interfaces.

**ExerciseControlPage.tsx refactor:**
- Exercise selector/creator at top
- Session ID shows active exercise ID
- Inject timeline: plot injects by `scheduled_offset` along 24h bar, color by status
- Inject builder form fully wired: type dropdown, target group toggles, content textarea, offset time input, "Commit Inject" calls API
- "EMERGENCY PAUSE" calls `updateExercise(id, { status: 'paused' })`
- Participant table: show 3-4 static mock rows for demo realism
- System control log: fetch from activity log filtered by exercise events

---

## Verification Plan

1. **Phase 0:** Start backend, check `data/emissary.db` file is created with all 5 tables
2. **Phase 1:** Create a COA via API, see it appear in Kanban board, drag between columns, verify status updates. Generate COA options from an analysis objective.
3. **Phase 2:** Create COAs and briefings, check KPI cards update. Verify map renders with markers. Check activity log shows creation events.
4. **Phase 3:** Generate a briefing from a COA, see it in the table, click to preview rendered markdown, approve it.
5. **Phase 4:** Create an exercise, add injects, see them on timeline, pause the exercise.
6. **Full flow:** Run an analysis on Search page → generate COA from results → generate briefing from COA → see monitoring dashboard reflect the activity.

---

## Files Modified/Created

| File | Action |
|------|--------|
| `src/db.py` | NEW — SQLite schema, helpers, activity logging |
| `src/api.py` | ADD ~30 endpoints across 4 feature sections |
| `frontend/src/types.ts` | ADD COA, Briefing, Exercise, Inject, KPI, ActivityEntry, MapMarker |
| `frontend/src/api.ts` | ADD ~20 API client functions |
| `frontend/src/components/MonitoringMap.tsx` | NEW — Leaflet map component |
| `frontend/src/pages/COAWorkspacePage.tsx` | REFACTOR from stub to functional |
| `frontend/src/pages/MonitoringPage.tsx` | REFACTOR from stub to functional |
| `frontend/src/pages/BriefingsPage.tsx` | REFACTOR from stub to functional |
| `frontend/src/pages/ExerciseControlPage.tsx` | REFACTOR from stub to functional |
| `frontend/package.json` | ADD @dnd-kit/core, @dnd-kit/sortable |
