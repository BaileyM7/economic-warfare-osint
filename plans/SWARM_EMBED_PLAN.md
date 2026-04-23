# Plan: Embed Swarm Backend into Emissary's Service

## Context

The Emissary `/wargame` tab was integrated as a **ported frontend talking to
a separately-deployed swarm microservice** (see `docs/wargame-deployment.md`).
That design gave us clean isolation but required provisioning a second
Render web service, adding ~$7/mo and a CORS boundary.

This plan describes a **major shift**: collapsing swarm's backend into
Emissary's existing FastAPI process so the wargame tab works on the
already-deployed `emissary-v1` branch without a second web service.

**Important trade-off**: this plan keeps all swarm functionality intact but
dissolves the isolation boundary. A swarm startup failure would crash
Emissary. A dependency upgrade would affect both apps. We pick this only
if the cost/ops savings outweigh the blast-radius risk.

## Goal state

- One Render web service (`emissary`) runs both apps.
- Emissary mounts swarm's FastAPI app at `/api/wargame/*`; WebSocket at `/api/wargame/ws/...`.
- Frontend calls use same-origin relative URLs — no CORS.
- Postgres + Redis are separate Render resources (unavoidable).
- Feature flag `WARGAME_ENABLED` gates the embed; disabled = zero impact on Emissary's legacy behavior.

## Architecture diagram

```
┌──────────────────────────────────────────────────────────────┐
│  Emissary web service (single container)                     │
│                                                              │
│  FastAPI app (uvicorn)                                       │
│  ├─ / (SPA, served from frontend/dist)                       │
│  ├─ /api/* (Emissary routes: coa, monitoring, briefings…)    │
│  ├─ /api/admin/* (analytics)                                 │
│  └─ /api/wargame/* ◄─ mounted swarm FastAPI app              │
│      ├─ scenarios/*                                          │
│      ├─ simulations/*                                        │
│      ├─ countries/*                                          │
│      └─ ws/simulations/{id} (WebSocket)                      │
│                                                              │
│  In-process state:                                           │
│  - SQLite (Emissary) at data/emissary.db                     │
│  - asyncpg pool (swarm) to Postgres                          │
│  - Redis connection (swarm) to Render KV store               │
└──────────────────────────────────────────────────────────────┘
          │                               │
          ▼                               ▼
  ┌───────────────────┐         ┌─────────────────┐
  │ Render Postgres   │         │ Render Redis    │
  │ (+ pgvector)      │         │ (Key Value)     │
  └───────────────────┘         └─────────────────┘
```

## Phased implementation

### Phase 0 — Risk reconnaissance (2–3 hours, reversible)

Before touching any code, verify the assumptions this plan depends on.

- [ ] **Dep compatibility check**: run `uv pip compile` against a merged
      pyproject that combines both dep lists. Confirm no version conflicts.
      Key pairs to watch:
      - `anthropic` (Emissary >=0.52.0, swarm >=0.39.0) — use Emissary's floor
      - `httpx` (both >=0.27) — should be fine
      - `pandas` (both >=2.2) — should be fine
      - `pydantic` (Emissary >=2.0, swarm >=2.9.2) — use swarm's floor
      - `fastapi`, `uvicorn` — swarm's are slightly older but compatible
- [ ] **Memory budget**: measure swarm's container memory in its own deploy.
      If >400MB baseline, Emissary's Render Starter (512MB) is too tight.
      Decide whether to upgrade to Standard (2GB, ~$25/mo).
- [ ] **Startup time**: measure swarm's cold boot. If Alembic migrations
      take >30s, Render's health-check window needs tuning.
- [ ] **Python version alignment**: both require `>=3.12`. ✓

**Abort if:** conflicts force downgrading a shared dep below what
Emissary needs, memory doesn't fit, or a swarm dep has native build
requirements that Render's buildpack can't satisfy.

### Phase 1 — Directory restructure (half-day)

Copy swarm's Python source into Emissary's `src/` tree under new names
so its imports don't collide with Emissary's existing packages.

- [ ] Copy `swarm/src/backend/*` → `src/wargame_backend/*`
- [ ] Copy `swarm/src/ai/*` → `src/wargame_ai/*`
- [ ] Copy `swarm/src/shared/*` → `src/wargame_shared/*`
- [ ] Copy `swarm/alembic/`, `swarm/alembic.ini` → `src/wargame_backend/alembic/`,
      `src/wargame_backend/alembic.ini`
- [ ] Global find-replace on the copied files:
      - `from src.backend` → `from src.wargame_backend`
      - `from src.ai` → `from src.wargame_ai`
      - `from src.shared` → `from src.wargame_shared`
- [ ] Verify `src.wargame_backend.app.main:app` still imports successfully
      in isolation.

**Critical files to audit after rename:**
- [src/wargame_backend/app/main.py](src/wargame_backend/app/main.py) — FastAPI app entry
- [src/wargame_backend/app/config.py](src/wargame_backend/app/config.py) — Pydantic settings
- [src/wargame_backend/app/db/session.py](src/wargame_backend/app/db/session.py) — async engine
- [src/wargame_backend/app/ws/simulations.py](src/wargame_backend/app/ws/simulations.py) — WebSocket
- [src/wargame_backend/alembic/env.py](src/wargame_backend/alembic/env.py) — migration config

### Phase 2 — Dependency merge (half-day)

Add swarm's deps to Emissary's `pyproject.toml` as an **optional extra**,
so installing without the extra leaves Emissary behavior unchanged.

```toml
[project.optional-dependencies]
wargame = [
    "sqlalchemy[asyncio]>=2.0.35",
    "alembic>=1.13.3",
    "asyncpg>=0.30.0",
    "pgvector>=0.3.6",
    "redis>=5.2.0",
    "pydantic-settings>=2.6.1",
    "tenacity>=9.0.0",
    "structlog>=24.4.0",
    "langgraph>=0.2.40",
    "langchain>=0.3.7",
    "langchain-anthropic>=0.2.4",
    "langchain-community>=0.3.5",
    "pyarrow>=18.0.0",
    "dlt[postgres]>=1.3.0",
    "pyyaml>=6.0.2",
    "orjson>=3.10.11",
    "python-multipart>=0.0.17",
    "slowapi>=0.1.9",
]
```

- [ ] Bump `anthropic` and `pydantic` floors if needed to satisfy swarm
- [ ] Run `uv sync --extra wargame` locally; confirm a clean lock
- [ ] Update `build.sh` to install the `wargame` extra when `WARGAME_ENABLED=1`

### Phase 3 — FastAPI mount + feature flag (half-day)

Wire swarm's app as a sub-app of Emissary's, gated by env var.

- [ ] In [src/api.py](src/api.py), add after existing router includes:

```python
import os
if os.environ.get("WARGAME_ENABLED", "").lower() in {"1", "true", "yes"}:
    from src.wargame_backend.app.main import app as wargame_app
    app.mount("/api/wargame", wargame_app)
    logger.info("Wargame subapp mounted at /api/wargame")
```

- [ ] Swarm's own lifespan/startup events (Alembic upgrade, ingest) run
      when the subapp is mounted. Verify that happens exactly once per
      container boot, not on every request.
- [ ] Add a `/api/wargame/healthz` alias that returns the subapp's `/healthz`
      so existing smoke-test scripts keep working.

### Phase 4 — Frontend rewire (1 hour)

The Emissary frontend currently calls `VITE_SWARM_API_URL`. Point it at
the same-origin `/api/wargame` path.

- [ ] In [frontend/src/wargame/lib/api/client.ts](frontend/src/wargame/lib/api/client.ts):
      change the default fallback from `http://localhost:8000` to
      `/api/wargame`. The absolute URL env var stays available for cases
      where someone wants to run swarm separately.
- [ ] In [frontend/src/wargame/hooks/useSimStream.ts](frontend/src/wargame/hooks/useSimStream.ts):
      default `VITE_SWARM_WS_URL` to `${window.location.protocol === 'https:' ? 'wss:' : 'ws:'}//${window.location.host}/api/wargame`.
- [ ] Remove the `VITE_SWARM_API_URL` / `VITE_SWARM_WS_URL` injection
      from [render.yaml](render.yaml) — no longer needed.

### Phase 5 — Render resources + config (1 hour)

- [ ] Simplify [render.yaml](render.yaml): drop `swarm-api` service, keep
      only `emissary`, `swarm-redis`, `swarm-db`.
- [ ] Add Emissary env vars to the Blueprint:
      - `WARGAME_ENABLED=1`
      - `DATABASE_URL` from swarm-db
      - `REDIS_URL` from swarm-redis
      - `ANTHROPIC_API_KEY` (already set; reused)
      - `AGENT_MODEL=claude-sonnet-4-6`, `ARBITER_MODEL=claude-opus-4-6`,
        `AGENT_RUNNER_IMPL=langgraph`
- [ ] Upgrade Emissary's Render plan from Free → Starter ($7/mo) — needed
      to keep the container awake for WebSocket sims.

### Phase 6 — Migrations + seed on boot (half-day)

Emissary's `init_db()` for SQLite needs to coexist with swarm's Alembic
migrations for Postgres. They don't conflict (different DBs), but need to
be orchestrated.

- [ ] Wrap `alembic upgrade head` in an idempotent startup hook inside
      swarm's FastAPI lifespan. If it's already at head, it's a no-op.
- [ ] Gate the expensive ingest runner behind a separate env var
      (`WARGAME_AUTO_INGEST=1`) so we don't re-ingest the data lake on
      every container boot. Default off; run manually via Render shell.

### Phase 7 — Testing (half-day)

- [ ] **Flag OFF**: boot Emissary without `WARGAME_ENABLED` → all legacy
      tabs work, `/wargame` shows DEMO mode only, EXECUTE fails cleanly.
- [ ] **Flag ON, no Postgres**: boot Emissary with `WARGAME_ENABLED=1`
      but no `DATABASE_URL` → swarm should log a clear error and NOT
      mount, leaving Emissary healthy. (Verify the mount is wrapped in
      try/except.)
- [ ] **Flag ON, full stack**: boot Emissary + Postgres + Redis locally
      (docker compose from swarm/ for the infra). Run a Taiwan 2027 sim
      end-to-end through the UI.
- [ ] **Regression**: re-run the earlier Playwright sweep on COA,
      Monitoring, Briefings, Search, Admin to confirm no side effects.

### Phase 8 — Deploy + verify (1 hour)

- [ ] Push `wargame-v1` (or a new branch) with all changes.
- [ ] In Render: provision Postgres + Redis resources (manually or via
      Blueprint registration — same as before).
- [ ] Set `WARGAME_ENABLED=1` + DB/Redis URLs + model env vars on the
      `emissary` service.
- [ ] Trigger redeploy; watch boot logs for Alembic success.
- [ ] SSH to Emissary's webshell, run one-time seed:
      ```bash
      psql "$DATABASE_URL" -c "CREATE EXTENSION IF NOT EXISTS vector;"
      python -m src.wargame_backend.ingest.runner --sources=gdelt,worldbank
      ```
- [ ] Smoke test from a browser: log in → /wargame → Taiwan 2027 preset → Execute.

## Reusable patterns already in Emissary

- **Feature flag gating**: the existing analytics middleware in
  [src/analytics.py](src/analytics.py) is a similar conditional wiring
  pattern (mounts only when user implements classify_endpoint).
- **Optional deps via extras**: Emissary's `pyproject.toml` already has
  `dev` as a dep group — `wargame` follows the same pattern.
- **FastAPI include_router pattern**: Emissary uses `include_router` for
  its own routers; for swarm we use `app.mount` instead because swarm is
  a full sub-FastAPI, not just a router.

## Kill switch / revert plan

If anything breaks in production:

1. **Immediate**: unset `WARGAME_ENABLED` in Render → redeploy. Emissary's
   legacy functionality returns with zero risk. Frontend still shows the
   wargame tab but EXECUTE fails (same as today without swarm deployed).
2. **Full revert**: git revert the embed commits on `emissary-v1`. The
   separate-service render.yaml from wargame-v1 is still a reachable state.
3. **Orphaned resources**: Render Postgres + Redis can stay idle or be
   deleted (~$10/mo saved if deleted).

## Verification (end-to-end)

After deploy with `WARGAME_ENABLED=1`:

```bash
# Same-origin health checks (no CORS)
curl https://emissary.onrender.com/api/wargame/healthz        # → {"status":"ok"}
curl https://emissary.onrender.com/api/wargame/api/countries  # → 10 countries

# From the browser: log in, /wargame, Taiwan 2027 preset, Execute
# Expect: Turn counter flips to 1/3, arcs pulse, Decision Log opens.
```

## Effort estimate

| Phase | Time |
|---|---|
| 0. Risk recon | 2–3 hrs |
| 1. Directory restructure | 4 hrs |
| 2. Dep merge | 4 hrs |
| 3. Mount + flag | 4 hrs |
| 4. Frontend rewire | 1 hr |
| 5. Render config | 1 hr |
| 6. Migrations orchestration | 4 hrs |
| 7. Testing | 4 hrs |
| 8. Deploy + verify | 1 hr |
| **Total** | **~25 hrs / 3 working days** |

Add contingency buffer 50% → **budget 4–5 working days**.

## Out of scope

- Migrating Emissary's SQLite to Postgres (would be a separate shift).
- Porting swarm's own frontend tests (not needed — frontend is already
  ported).
- Persistent disk for Emissary's SQLite (orthogonal; relevant regardless).

## Decision gate before executing

**Answer these before I write any code:**

1. **Memory headroom**: do you want to pre-upgrade Emissary's Render plan
   from Free → Starter ($7/mo), or gamble that 512MB is enough?
2. **Branch strategy**: land the refactor on `wargame-v1` first (safer
   staging), or directly on a new branch off `emissary-v1`?
3. **Risk tolerance**: if Phase 1/2 reveals a dep conflict that would
   require patching swarm's source, do we proceed with patches or abandon
   the embed path? (Patches mean diverging from upstream swarm.)
4. **Rollback timing**: is there a customer demo in the next week where
   Emissary absolutely cannot be red? If yes, start embed work AFTER the
   demo; before if not.

## Recommendation

I'll execute this plan if you confirm you want the embed path despite
the trade-offs. Otherwise the simpler "separate-service via Blueprint"
path on `wargame-v1` is ready to ship today — requires only Render
dashboard clicks, no code changes.
