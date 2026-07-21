# 03 — Backend & API

How the FastAPI process is wired, and the **complete endpoint catalog** (verified against the
route decorators, June 2026).

## App construction (`src/api.py`)

[src/api.py](../src/api.py) (~334 lines after the Phase-2 decomposition) is now **pure app
composition** — the analysis/search endpoints moved out into dedicated routers in `src/routers/`
(see the table below). At import time it:

1. Creates `app = FastAPI(...)`.
2. Adds **CORS** (`CORS_ORIGINS`, default `localhost:5173/3000`).
3. Adds **rate limiting** (`src/common/rate_limit.py`, slowapi; Redis-backed if `REDIS_URL`, else in-memory).
4. Adds **`UsageTrackingMiddleware`** ([src/analytics.py](../src/analytics.py)) → logs every request to `usage_events`.
5. **Includes the routers** (see auth column below).
6. Optionally **mounts the wargame subapp** at `/api/wargame` if `WARGAME_ENABLED` is truthy,
   and (if `WARGAME_DEBUG_ERRORS`) adds `/api/wargame-debug/lifespan`.
7. **Mounts static files**: `/assets` → `frontend/dist/assets`, plus a catch-all
   `GET /{filename:path}` SPA fallback that serves `frontend/dist/index.html`.
8. On startup: `init_db()`, optional `seed_mock_data()` (if `EMISSARY_MOCK_DATA`), enters the
   wargame subapp lifespan, and opens a browser unless `RENDER` is set.

### Routers and how they're mounted

| Router | File | Prefix | Auth applied at include |
|--------|------|--------|-------------------------|
| auth | [routers/auth.py](../src/routers/auth.py) | `/api/auth` | none (login is public) |
| admin | [routers/admin.py](../src/routers/admin.py) | `/api/admin` | **`require_admin`** |
| coa | [routers/coa.py](../src/routers/coa.py) | `/api` | `require_auth` |
| monitoring | [routers/monitoring.py](../src/routers/monitoring.py) | `/api` | `require_auth` |
| briefings | [routers/briefings.py](../src/routers/briefings.py) | `/api` | `require_auth` |
| risk_feed | [routers/risk_feed.py](../src/routers/risk_feed.py) | `/api/risk-feed` | `require_auth` |
| watchlist | [routers/watchlist.py](../src/routers/watchlist.py) | `/api/watchlist` | `require_auth` |
| notifications | [routers/notifications.py](../src/routers/notifications.py) | `/api/notifications` | none (cron token / webhook) |
| orchestrator | [routers/orchestrator.py](../src/routers/orchestrator.py) | `/api` | `require_auth` |
| entity | [routers/entity.py](../src/routers/entity.py) | `/api` | `require_auth` |
| person | [routers/person.py](../src/routers/person.py) | `/api` | `require_auth` |
| sector | [routers/sector.py](../src/routers/sector.py) | `/api` | `require_auth` |
| risk | [routers/risk.py](../src/routers/risk.py) | `/api` | `require_auth` |
| vessel | [routers/vessel.py](../src/routers/vessel.py) | `/api` | `require_auth` |
| screening | [routers/screening.py](../src/routers/screening.py) | `/api` | `require_auth` |
| sayari | [routers/sayari.py](../src/routers/sayari.py) | `/api` | `require_auth` |
| sanctions_impact | [routers/sanctions_impact.py](../src/routers/sanctions_impact.py) | `/api` | `require_auth` |
| followup | [routers/followup.py](../src/routers/followup.py) | `/api` | `require_auth` |

The analysis/search endpoints that previously lived directly on `app` in `api.py` now live in
the bottom group of routers above (same paths, same `require_auth` applied at include time).

## Auth (`src/auth.py`, `src/routers/auth.py`)

- **Scheme:** HMAC-signed bearer tokens. `EMISSARY_AUTH_SECRET` signs them (default
  `dev-secret-change-me` — must be overridden in prod).
- **Dependencies:** `require_auth` (any logged-in user) and `require_admin` (username in
  `EMISSARY_ADMIN_USERS`).
- **Login:** `POST /api/auth/login` checks against `EMISSARY_DEMO_USERNAME` / `EMISSARY_DEMO_PASSWORD`
  (defaults `analyst` / `demo`) and registered users in the `users` table; returns a token.
- **Frontend:** stores the token in `localStorage` (`emissary_token`); a 401 clears it and redirects to `/login`.

## Endpoint catalog

### Public / infrastructure (no auth)
| Method | Path | Purpose |
|--------|------|---------|
| GET | `/` | Serve the SPA `index.html` (503 if frontend not built) |
| GET | `/api/health` | Health + config validation |
| GET | `/{filename:path}` | Static file or SPA fallback |
| WS | `/ws/monitoring?token=…` | Live activity feed (token checked in handler) |
| GET | `/api/wargame-debug/lifespan` | Wargame lifespan error (only if `WARGAME_DEBUG_ERRORS`) |

### Analysis engine (auth)
| Method | Path | Purpose |
|--------|------|---------|
| GET | `/api/tools` | List available tools (from `ToolRegistry`) |
| POST | `/api/analyze` | Start async analysis → `{analysis_id, session_id}`. Optional `session_id` continues a thread |
| GET | `/api/analyze/{analysis_id}` | Poll status / progress / result (also echoes `session_id`) |
| POST | `/api/analyze/sync` | Run analysis synchronously (blocks). Threads only if given a `session_id` |
| POST | `/api/followup` | Follow-up Q grounded in `context` **or** a `session_id` (the old duplicate `/api/follow-up` was removed in Phase 2) |

#### Sessions / working memory

`POST /api/analyze` returns a **`session_id`** and threads the conversation server-side via
[common/agent_memory.py](../src/common/agent_memory.py) — turns, the last assessment, and the
entities it surfaced. Pass that id back on the next `/api/analyze` or `/api/followup` to continue
the thread ("what else is exposed to *that* supply chain?" resolves against the stored entities).

All of it is **additive**: omit `session_id` and behaviour is exactly as before. On `/api/followup`,
a client-supplied `context` still **wins** and produces a byte-identical prompt — so the current
frontend, which posts the whole assessment back on every question, is unaffected. Omit `context`
and pass `session_id` instead and the server hydrates it from working memory (`wm.assessment` is
the same `ImpactAssessment.model_dump()` shape the browser posts), which is how the client will
eventually stop shipping hundreds of KB per follow-up.

Storage: Redis when `REDIS_URL` is set (7-day TTL, **plain `SET`/`GET`/`EXPIRE` — no `FT.*`, so it
works on Render's Valkey keyvalue**), otherwise a process-local `TTLCache`. Threads are scoped by
the authenticated username, checked inside the seam — another user's `session_id` reads as absent
and is never hijackable. Redis being down degrades to "no memory", never a 500.
Check the mode: `GET /api/health` → `{"working_memory": {"backend": "redis"|"memory"}}`.

#### Long-term memory ([routers/memory.py](../src/routers/memory.py))

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/api/memory` | List everything remembered about the caller's prior work |
| GET | `/api/memory/search?q=` | Semantic (or lexical-fallback) search over the caller's memories |
| DELETE | `/api/memory/{memory_id}` | Delete one of the caller's memories |

Beyond the *session* thread above, Emissary keeps **durable, extracted facts** across sessions
([common/agent_memory.py](../src/common/agent_memory.py) long-term half). After each analysis a
background Haiku pass extracts reusable facts (ownership, sanctions status, supply-chain links) —
but **only** ones citing a source the analysis actually used (the anti-poisoning guard; see
[orchestrator/memory_extract.py](../src/orchestrator/memory_extract.py)). Before the *next*
analysis the orchestrator recalls this analyst's relevant prior facts and injects them into the
decompose + synthesis prompts as **UNVERIFIED prior work** — never as a cited source — so a
Friday question builds on Monday's findings ("what else is exposed to *that* supply chain?").

Storage: **SQLite `agent_memories` is the system of record**; a per-user Redis vector index is a
derived accelerator (needs Redis 8 + a Voyage key). Recall is semantic when both are present,
otherwise a lexical scan — always scoped to the caller. Analysts can see and delete their own
memories (the endpoints above), which is what makes the poisoning guard auditable in practice.
Check the mode: `GET /api/health` → `{"long_term_memory": {"backend": "redis"|"lexical"}}`.

#### Semantic pre-warm cache ([common/semantic_cache.py](../src/common/semantic_cache.py))

The pre-warm cache replays a warmed analysis instead of paying a ~4.5-min cold run. Phase 6
upgraded query matching from exact-string / Jaccard to **semantic** (when a Voyage key is set):

- **`POST /api/analyze/suggest`** ("Did you mean…?") now matches a *reworded* demo question, not
  just a token-overlapping one. Always suggest-only — a human confirms — so it's the safe,
  always-on upgrade. Response gains `backend` (`vector`|`lexical`).
- **Auto-replay** — answering a reworded question from a warmed one *without* confirmation — is
  **opt-in** (`EMISSARY_SEMANTIC_REPLAY`, default off) and **entity-signature gated**. Measured on
  the real API, entity swaps overlap real paraphrases (Fujian Jinhua→SMIC scores **0.961**, higher
  than a genuine paraphrase's 0.956), so **no distance threshold is safe alone**. Auto-replay
  requires high similarity AND an identical entity signature (proper nouns / acronyms / years); a
  differing signature is demoted to a suggestion regardless of cosine. When it does fire, the first
  event emitted is the disclosure (`replay_notice`) and `AnalysisStatus.replayed_from` is set;
  `POST /api/analyze {"force_fresh": true}` (the "run the exact question" button) bypasses it.
- The **lexical fallback can never auto-replay** — a missing Voyage key degrades to suggest-only,
  never a silent wrong answer. `GET /api/health` → `{"semantic_cache": {"suggest_backend": …,
  "auto_replay": bool}}`. Payloads stay in diskcache; this layer only resolves the query string.

#### Analysis state store ([common/analyses.py](../src/common/analyses.py))

Live analysis status/progress/events/result is held in an `AnalysisStore` — `InMemoryAnalysisStore`
(default; process-local `TTLCache`, byte-identical to before) or `RedisAnalysisStore` (RedisJSON,
1h TTL), selected by `ANALYSES_BACKEND=memory|redis`. The Redis backend makes analysis state
**survive a restart and be shared across web instances** (closes fragility **F3**): the write path
is explicit methods, and Redis appends use atomic `JSON.ARRAPPEND` + `JSON.ARRTRIM` so concurrent
writers can't clobber each other. Falls back to memory (loudly) if Redis JSON is unavailable, and
never 500s a running analysis on a Redis hiccup. `GET /api/health` →
`{"analysis_store": {"backend": "redis"|"memory"}}`.

### Entity / search (auth) — in the analysis/search routers (`src/routers/`)
| Method | Path | Purpose |
|--------|------|---------|
| POST | `/api/sanctions-impact` | Deterministic stock-impact projection (13 historical comparables) |
| POST | `/api/entity-graph` | Build vis.js entity graph for a query. Nodes carry opt-in viz hints (`value` → size by connectivity, `riskLevel`) and edges a `width` (tie strength); `meta.summary` gives counts by type + sanctioned/high-risk digest (issue #28). All hint fields are additive — older clients ignore them |
| POST | `/api/resolve-entity` | Classify text → entity type + confidence |
| POST | `/api/person-profile` | Person dossier: affiliations, offshore, risk factors |
| POST | `/api/person/search` | Person candidate search |
| POST | `/api/person/network` | Person relationship network |
| POST | `/api/sector-analysis` | Sector exposure + geopolitical tensions |
| POST | `/api/vessel-track` | Vessel particulars, route, port calls (AIS + OpenSanctions + fixtures) |
| POST | `/api/entity-risk-report` | Full risk report for an entity |
| POST | `/api/sanctions/screen-batch` | Batch sanctions screening of names |
| POST | `/api/sayari/resolve` | Sayari entity resolution (premium) |
| POST | `/api/sayari/related` | Sayari relationship traversal |
| POST | `/api/sayari/ubo` | Sayari ultimate beneficial owners |
| POST/GET/DELETE | `/api/knowledge/entities`[`/{entity_id}`] | Persistent graph knowledge store (issue #29) — **team-wide/shared** saved entities of any type; upsert deduped on `entity_id`, `created_by` records provenance |
| POST/GET/DELETE | `/api/knowledge/edges`[`/{edge_id}`] | Saved relationships (deduped on source+target+type); delete of an entity cascades to its incident edges |
| GET | `/api/knowledge/graph` | The whole saved store as a vis.js graph (reuses the shared `node()` factory), ready to load into GraphViewer |
| POST | `/api/discover-actions` | Target generation (issue #31): given an entity + an optional proposed action, suggest additional non-conflicting actions, grounded in the entity's knowledge-graph neighbors. Degrades to graph-context-only when no LLM client is configured |
| POST | `/api/entity/similar` | Semantic similarity / link prediction (issue #30): "find entities with similar characteristics." Ranks saved entities vs a target (by `entity_id` or ad-hoc `name`) with an explainable basis. Backend is `lexical` (default, offline) or `embedding` (local sentence-transformers, `similarity` extra); embedding falls back to lexical when the dep is absent |

### Dashboard — COA, briefings, monitoring (auth)
| Method | Path | Purpose |
|--------|------|---------|
| GET/POST | `/api/coa` | List / create COAs |
| GET/PUT/DELETE | `/api/coa/{coa_id}` | Read / update / delete a COA |
| POST | `/api/coa/generate` | AI-generate COA options (rate-limited `3/min; 30/day`) |
| GET/POST | `/api/briefing` | List / create briefings |
| GET/PUT/DELETE | `/api/briefing/{briefing_id}` | Read / update / delete a briefing |
| POST | `/api/briefing/generate` | AI-generate a briefing from a COA/analysis (rate-limited) |
| GET | `/api/monitoring/kpis` | Dashboard KPIs |
| GET | `/api/monitoring/activity` | Activity-log entries |
| GET | `/api/monitoring/map-data` | Map markers |
| GET | `/api/monitoring/macro` | Macro indicators (USD/CNY, Brent, VIX, DXY) |

### Dashboard — risk feed + watchlist (auth)
| Method | Path | Purpose |
|--------|------|---------|
| GET | `/api/risk-feed` | Per-user risk feed |
| POST | `/api/risk-feed/refresh` | Force refresh |
| GET | `/api/risk-feed/{item_id}` | Single feed item |
| POST | `/api/risk-feed/{item_id}/prepare-coa` | Enrich a feed item into a COA payload |
| GET/POST | `/api/watchlist` | List / add watchlist items |
| PATCH/DELETE | `/api/watchlist/{item_id}` | Update / remove |
| POST | `/api/watchlist/resolve` | Resolve a name → entity kind + category (rate-limited `30/min`) |
| GET | `/api/watchlist/suggestions` | Suggested watchlist entities |

### Admin (require_admin)
| Method | Path | Purpose |
|--------|------|---------|
| GET | `/api/admin/usage` | Usage analytics (`?days=`) |
| GET/POST | `/api/admin/enrollments` | List / enroll notification recipients |
| DELETE | `/api/admin/enrollments/{username}` | Unenroll |
| POST | `/api/admin/enrollments/{username}/test-sms` | Send a test SMS |
| POST | `/api/admin/enrollments/{username}/test-email` | Send a test email |
| POST/DELETE | `/api/priorities`[`/{id}`] | Set / remove a team-wide collection priority (issue #32) at country/sector/company level, with a weight that boosts matching risk-feed items. **Writes are admin-only; `GET /api/priorities` is readable by any analyst** (the priorities are global/shared) |

### Notifications (cron token / webhook, not bearer auth)
| Method | Path | Purpose |
|--------|------|---------|
| POST | `/api/notifications/send-weekly-digest` | Build + send the weekly digest (requires `X-Cron-Token`) |
| POST | `/api/notifications/twilio/sms-webhook` | Inbound Twilio webhook (STOP/HELP, replies) |

### Wargame subapp (mounted at `/api/wargame`, gated by `WARGAME_ENABLED`)
The subapp's routers use their own internal `/api/...` prefixes, so the **full** paths
double up. Documented in detail in [07-submodules/swarm-wargame.md](07-submodules/swarm-wargame.md):

| Method | Full path | Purpose |
|--------|-----------|---------|
| GET | `/api/wargame/healthz`, `/api/wargame/readyz` | Liveness / readiness |
| GET | `/api/wargame/api/countries`, `/{iso3}` | Country list / detail |
| GET/POST | `/api/wargame/api/scenarios` (+ `/{id}`, `/extract-events`) | Scenario CRUD + LLM extraction |
| POST/GET | `/api/wargame/api/simulations` (+ `/{id}`, `/{id}/abort`) | Start / read / abort a sim |
| GET | `/api/wargame/api/events`, `/api/sim-events` | Data-lake + sim-event queries |
| WS | `/api/wargame/ws/simulations/{sim_id}` | Live simulation event stream |

## State, persistence, and background work

- **SQLite** ([src/db.py](../src/db.py)) — `data/emissary.db`. Tables: `coas`, `briefings`,
  `exercises`, `injects`, `activity_log`, `usage_events`, `watchlist_items`, `users`,
  `notification_log`, `saved_entities`, `saved_edges` (the issue-#29 knowledge store),
  `priorities` (issue-#32 team priorities). Raw SQL via `get_db()`; `init_db()` is
  idempotent; `seed_mock_data()`
  loads demo data when `EMISSARY_MOCK_DATA` is set — **seed-only-if-empty** (takes a `force=`
  param) so deploys no longer wipe analyst-created data.
- **In-memory stores** — `_analyses` (analysis status/results) now lives in
  [src/common/analyses.py](../src/common/analyses.py); the `_ws_manager` WebSocket connection
  list stays in `api.py`. Both are **process-local**: lost on restart, not shared across
  workers. See [08](08-fragility-map.md).
- **Background tasks** — `/api/analyze` and the briefing/COA generators use `asyncio.create_task`
  for fire-and-forget work; clients poll for completion.
- **Rate limiting** — `src/common/rate_limit.py`. Default key is `user:<name>` (falls back to
  `ip:<addr>`); per-endpoint overrides on the LLM generators and `watchlist/resolve`.

## Wargame lifespan integration

When mounted, the subapp's lifespan (Alembic migrate → validate Postgres/Redis → build
`SimRunner`) is entered manually from the parent startup so a single Uvicorn process boots both
apps. If it fails, the parent still serves Emissary; the wargame routes error. Detail in
[07-submodules/swarm-wargame.md](07-submodules/swarm-wargame.md).
