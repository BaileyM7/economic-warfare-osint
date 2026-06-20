# 07 — Wargame (`src/wargame_*`, formerly the `swarm/` submodule)

The geopolitical multi-agent **wargame** backend now lives **only** at `src/wargame_*` in this
repo. The standalone `swarm/` git submodule that it was originally copied from **has been removed**,
so `src/wargame_*` is the single source of truth. (Historically this code existed twice — embedded
*and* as the submodule — but that is no longer the case.)

## TL;DR — which copy is live?

`src/wargame_*` is the **sole, canonical** wargame copy and what production runs. The table below
is **historical**: it records the now-removed `swarm/` submodule alongside the embedded tree that
superseded it.

| | `swarm/` submodule (REMOVED) | `src/wargame_*` (canonical) |
|---|---|---|
| Repo | `github.com/BaileyM7/swarm` (separate) — **no longer a submodule here** | in this repo (only copy) |
| Import roots | `app.*`, `ai.*`, `shared.*` | `wargame_backend.*`, `wargame_ai.*`, `wargame_shared.*` |
| How it runs | standalone (docker-compose / Fly.io) | **mounted at `/api/wargame`** by `src/api.py` when `WARGAME_ENABLED=1` |
| Status | **gone from this repo** — kept upstream for standalone/reference deploys | **authoritative — this is what production runs** |

**Verified:** the running app imports `wargame_backend.*` only. The embedded tree originated as an
**import-renamed copy** of `swarm/src/{backend,ai,shared}` with **two Render-specific patches**
(below); the embedding was done per [`plans/SWARM_EMBED_PLAN.md`](../archive/) (now archived).
With the submodule removed, there is no second copy to keep in sync.

### The two embedded-only patches
1. **In-process Alembic** — `src/wargame_backend/app/main.py` adds `_run_alembic_upgrade()`
   (runs `command.upgrade(cfg, "head")` in a worker thread at startup) so Render needs no separate
   release command.
2. **Postgres DSN normalization** — `src/wargame_backend/app/config.py` adds
   `_normalize_async_postgres()` to rewrite Render's `postgres://…` into `postgresql+asyncpg://…`
   for SQLAlchemy's async engine.

### Drift risk — RESOLVED (Phase 2)
This used to be a top fragility item: the embedded copy was a **manual fork** of the `swarm/`
submodule with no automated sync, so a fix in one copy could silently miss the other. **The
submodule has now been removed**, leaving `src/wargame_*` as the single source of truth — there is
no second copy to drift from. See [08-fragility-map.md](../08-fragility-map.md) (F2, resolved).
If you ever pull changes from the upstream `swarm` repo, re-apply the import rename + the two
patches by hand.

## What it does

A turn-based simulation where AI agents representing countries take actions across domains
(info, diplomatic, economic, cyber, kinetic) over up to `MAX_TURNS` (default 20). Built on
**LangGraph**:

- **Country agents** (`wargame_ai/agents/country_agent.py`) — Claude tool-use (`AGENT_MODEL`,
  default `claude-sonnet-4-6`) propose an action per turn from perceived world state + memory + recent real events.
- **Arbiter** (`wargame_ai/agents/arbiter.py`) — Claude (`ARBITER_MODEL`, default
  `claude-opus-4-6`) adjudicates simultaneous actions into outcomes + relationship deltas.
- **Sim loop** (`wargame_ai/sim/loop.py`) — orchestrates perceive → decide → arbitrate → emit;
  classifies actions on an **escalation ladder** (rungs 0–5).
- **Agent memory** (`wargame_ai/memory/`) — pgvector RAG over per-country memories, embedded with
  Voyage (`VOYAGE_API_KEY`/`EMBEDDING_MODEL`); falls back to a deterministic hash embedder if no key.
- **Runner switch** `AGENT_RUNNER_IMPL`: `null` (stub, deterministic — default) or `langgraph`
  (real Claude calls — what Render sets).

## API & WebSocket contract (mounted at `/api/wargame`)

The subapp's routers carry their own `/api/...` prefixes, so full paths **double up**:

| Method | Full path | Purpose |
|--------|-----------|---------|
| GET | `/api/wargame/healthz`, `/readyz` | Liveness / readiness (DB + Redis) |
| GET | `/api/wargame/api/countries` (+ `/{iso3}`) | Country list / detail |
| GET/POST | `/api/wargame/api/scenarios` (+ `/{id}`) | Scenario CRUD |
| POST | `/api/wargame/api/scenarios/extract-events` | LLM: prose → seed countries + events |
| POST | `/api/wargame/api/simulations` | Start a sim (`202`) |
| GET | `/api/wargame/api/simulations/{id}` | Sim status |
| POST | `/api/wargame/api/simulations/{id}/abort` | Abort |
| GET | `/api/wargame/api/events`, `/api/sim-events` | Data-lake + sim-event queries |
| WS | `/api/wargame/ws/simulations/{sim_id}` | Live event stream |

### WebSocket frames
Envelope `{frame_type, sim_id, seq, ts, payload}`. Server → client: `connected`, `turn_start`,
`sim_event`, `turn_end`, `sim_complete`, `error`, `heartbeat`. Client → server: `control`
(`pause` / `resume` / `abort`). Events are fanned out via **Redis pub/sub** (`sim:{sim_id}:events`).
The frontend consumer is `frontend/src/wargame/hooks/useSimStream.ts`.

## Data model (Postgres + pgvector)

Alembic-managed (4 migrations: initial, country persona, sim-event explainability, AIS positions).
Tables: `countries`, `relationships`, `data_sources`, `events`, `scenarios`, `simulations`,
`sim_events`, `agent_memory` (vector 1536), `country_personas`, `ais_positions`. Extensions:
`pgvector` (+ `pg_trgm`). Seeds in `wargame_shared/seeds/`: `countries.yaml` (10 countries — CHN,
TWN, USA, JPN, KOR, PHL, AUS, PRK, RUS, IND) and `taiwan_scenario.yaml`.

## Running it

- **In-repo (production):** set `WARGAME_ENABLED=1`; provide `DATABASE_URL`, `REDIS_URL`,
  `ANTHROPIC_API_KEY`, and `AGENT_RUNNER_IMPL=langgraph`. Install deps with `uv sync --extra wargame`.
  Env vars: see [06-deployment-render.md](../06-deployment-render.md). This is the only copy that
  ships here.
- **Standalone (upstream `swarm` repo, optional):** the original standalone deployment (its own
  `docker compose up` with Postgres, Redis, Adminer, backend, frontend; plus its `.env.example`,
  `docs/architecture.md`, and `fly.toml`) lives in the separate upstream `swarm` repo — it is no
  longer vendored here. See [../wargame-deployment.md](../wargame-deployment.md) for those notes.
- **Smoke test:** `scripts/check-swarm-connection.sh <swarm-url> <emissary-origin>`.

## Where to read more

- The upstream `swarm` repo's own `docs/architecture.md` — 900+ lines, the canonical sim/API spec
  (it lives in that separate repo; it is no longer a submodule here).
- Standalone deploy notes: [../wargame-deployment.md](../wargame-deployment.md).
- Frontend side: [frontend.md](frontend.md) (the wargame UI).
