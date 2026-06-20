# 06 — Deployment (Render)

How the app runs in production, every service in [render.yaml](../render.yaml), and the
**complete environment-variable matrix**.

## Topology

One Render **web service** runs everything (Emissary API + static SPA + embedded wargame
subapp). It's backed by a managed **Postgres** (`swarm-db`, with pgvector) and **Redis**
(`swarm-redis`) used by the wargame. A **cron** service fires the weekly email digest.

```
                 Render (region: virginia)
┌──────────────────────────────────────────────────────────┐
│  web: economic-warfare-osint   (plan: starter)            │
│    build:  ./build.sh                                     │
│    start:  uvicorn src.api:app --host 0.0.0.0 --port $PORT│
│    serves: /api/*, static SPA, /api/wargame/* (subapp)    │
│    disk:   1 GB @ /opt/render/project/src/data            │
│      │                     │                              │
│      ▼                     ▼                              │
│  swarm-db (Postgres16+pgvector)   swarm-redis (KeyValue)  │
└──────────────────────────────────────────────────────────┘
   cron: emissary-weekly-digest  → POST /api/notifications/send-weekly-digest  (Mon 12:00 UTC)
```

> **render.yaml was reconciled in Phase 2** with the audited prod reality: service name
> `economic-warfare-osint`, region `virginia`, a declared **1 GB persistent disk** at
> `/opt/render/project/src/data` (so the SQLite DB + cache survive deploys), `APP_ENV=production`,
> and `EMISSARY_MOCK_DATA=0`. The datastores are declared internal-only (`ipAllowList: []`), but
> the **live** `swarm-db` / `swarm-redis` were found open to `0.0.0.0/0` and still need to be
> tightened from the Render dashboard.

## Services (`render.yaml`)

### `economic-warfare-osint` (web)
- **Runtime:** Python; region `virginia`; `PYTHON_VERSION=3.12.7`, `NODE_VERSION=20.11.0`. Plan
  **starter** (bumped from free for WebSocket + LangGraph RAM). Branch **`emissary-v1`**,
  **auto-deploy on push**. Sets `APP_ENV=production` and `EMISSARY_MOCK_DATA=0`.
- **Disk:** a **1 GB persistent disk** mounted at `/opt/render/project/src/data` keeps the SQLite
  app DB + disk cache across deploys.
- **Build:** `./build.sh` (`pip install -r requirements.txt` → `vite build`; no submodules — `frontend/` is in-repo).
- **Start:** `uvicorn src.api:app --host 0.0.0.0 --port $PORT` (Render injects `$PORT`).

### `emissary-weekly-digest` (cron)
- Image `curlimages/curl`; schedule `0 12 * * 1` (Mondays 12:00 UTC ≈ 7 AM ET).
- `curl -X POST -H "X-Cron-Token: $NOTIFICATIONS_CRON_TOKEN" "$APP_BASE_URL/api/notifications/send-weekly-digest"`.
- Pulls `NOTIFICATIONS_CRON_TOKEN` + `APP_BASE_URL` from the web service.

### `swarm-redis` (Key Value) & `swarm-db` (Postgres)
- Redis: starter plan, region `virginia`, declared internal-only (`ipAllowList: []`). Wargame world-state pub/sub.
- Postgres 16, plan `basic-256mb` (cheapest tier allowing `CREATE EXTENSION vector`).
  **One-time setup** in the Render shell: `psql "$DATABASE_URL" -c "CREATE EXTENSION IF NOT EXISTS vector;"`.
- Alembic migrations run **in-process** at app startup (no separate release command).
- ⚠️ render.yaml declares both datastores `ipAllowList: []`, but the **live** instances were
  found open to `0.0.0.0/0` in the Phase-2 audit — close that from the Render dashboard.

## How the frontend is served

`build.sh` runs `vite build` against the **in-repo** `frontend/` source → `frontend/dist/`.
`src/api.py` mounts `/assets` → `frontend/dist/assets` and serves `frontend/dist/index.html` for `/`
and any unmatched path (SPA history-mode routing). No separate static host, and no submodule checkout.
Detail: [07-submodules/frontend.md](07-submodules/frontend.md).

## Environment-variable matrix

**Legend — "Set via":** `yaml` = literal value in render.yaml · `secret` = render.yaml `sync:false`
(set in dashboard) · `generated` = render.yaml `generateValue` · `linked` = from another service/DB
· `dashboard?` = not in render.yaml, relies on default unless set · `code` = read only in code.

### Core / required
| Var | Set via | Consumed by | Default / note |
|-----|---------|-------------|----------------|
| `ANTHROPIC_API_KEY` | secret | engine + wargame (Claude) | none — **required**; cleared in tests |
| `EMISSARY_AUTH_SECRET` | **dashboard?** | `src/auth.py` | `dev-secret-change-me` — **must override in prod** |
| `APP_BASE_URL` | secret | email links (CAN-SPAM) | `https://emissary.onrender.com` |
| `CORS_ORIGINS` | **dashboard?** | `src/api.py` | `localhost:5173,3000,127.0.0.1:5173` — set to the real frontend origin |
| `EMISSARY_ADMIN_USERS` | **dashboard?** | `src/auth.py` | `""` — without it, no one has admin |
| `PORT` | linked (Render) | uvicorn | injected by Render |
| `RENDER` | (Render auto) | `src/api.py` | presence skips the dev browser-open |

### Feature flags / mode
| Var | Set via | Consumed by | Default |
|-----|---------|-------------|---------|
| `WARGAME_ENABLED` | yaml `1` | `src/api.py` (mounts subapp) | off |
| `WARGAME_DEBUG_ERRORS` | dashboard? | `src/api.py` debug route | off |
| `EMISSARY_MOCK_DATA` | yaml `1` | `src/api.py` startup (`seed_mock_data`) | off |
| `RISK_FEED_MODE` | dashboard? | `routers/risk_feed.py` | `auto` |
| `EMISSARY_DEMO_USERNAME` / `EMISSARY_DEMO_PASSWORD` | dashboard? | `routers/auth.py` | `analyst` / `demo` |

### Engine model + cache
| Var | Set via | Consumed by | Default |
|-----|---------|-------------|---------|
| `CLAUDE_MODEL` | dashboard? | engine (`config.py`) | `claude-sonnet-4-20250514` |
| `CACHE_DIR` | dashboard? | `config.py` | `data/cache` |
| `CACHE_TTL_SECONDS` | dashboard? | `config.py` | `3600` |

> ⚠️ `CLAUDE_MODEL` (the engine) is **separate** from the wargame's `AGENT_MODEL` /
> `ARBITER_MODEL`. They configure different subsystems and different model families.

### Data-source keys (all optional; improve coverage/limits)
`FRED_API_KEY` (secret in yaml) · `COMTRADE_API_KEY` / `UN_COMTRADE_KEY` · `OPENSANCTIONS_API_KEY`
· `OPENCORPORATES_API_KEY` · `ACLED_API_KEY` + `ACLED_EMAIL` + `ACLED_PASSWORD` + `REFRESH_TOKEN`
· `AISSTREAM_API_KEY` (+ `AISSTREAM_SAMPLE_SECONDS`) · `FINNHUB_API_KEY` · `SAYARI_CLIENT_ID` +
`SAYARI_CLIENT_SECRET` (or `SAYARI_API_KEY`) · `TRADE_GOV_API_KEY` · `EIA_API_KEY` ·
`SEC_EDGAR_USER_AGENT` (override the default SEC EDGAR User-Agent header). See
[04-tools-and-data.md](04-tools-and-data.md).

### Notifications
| Var | Set via | Default |
|-----|---------|---------|
| `NOTIFICATIONS_ENABLED` | yaml `true` | `false` |
| `TWILIO_ACCOUNT_SID` / `TWILIO_AUTH_TOKEN` / `TWILIO_FROM_PHONE` | secret | none |
| `SENDGRID_API_KEY` | secret | none |
| `NEWSLETTER_FROM_EMAIL` | secret | `noreply@emissary.demo` |
| `NEWSLETTER_FROM_NAME` | yaml | `Emissary Weekly Brief` |
| `SMS_DAILY_CAP_PER_USER` | yaml `3` | `3` |
| `NOTIFICATIONS_CRON_TOKEN` | generated | none (required for the cron endpoint) |
| `NOTIFICATIONS_ALLOWLIST` | secret | `""` (no allowlist) |
| `TWILIO_STUB_MODE` / `SENDGRID_STUB_MODE` | code (dev only) | `false` — **never set in prod** |

### Wargame subapp (`src/wargame_backend/app/config.py`)
| Var | Set via | Default |
|-----|---------|---------|
| `DATABASE_URL` | linked (`swarm-db`) | `postgresql+asyncpg://swarm:swarm@localhost:5432/swarm` |
| `REDIS_URL` | linked (`swarm-redis`) | `redis://localhost:6379/0` |
| `AGENT_MODEL` | yaml `claude-sonnet-4-6` | `claude-sonnet-4-6` |
| `ARBITER_MODEL` | yaml `claude-opus-4-6` | `claude-opus-4-6` |
| `AGENT_RUNNER_IMPL` | yaml `langgraph` | `null` (stub) |
| `VOYAGE_API_KEY` | dashboard? | none (falls back to a hash embedder) |
| `EMBEDDING_MODEL` / `EMBEDDING_DIMS` | dashboard? | `voyage-3` / `1536` |
| `MAX_TURNS` / `MAX_CONCURRENT_SIMS` | dashboard? | `20` / `4` |
| `AGENT_MAX_TOKENS` / `AGENT_PACE_SECONDS` / `DISABLED_AGENT_TOOLS` | dashboard? | `2048` / `0` / `""` |
| `NO_ACTION_STREAK_THRESHOLD` / `DOMAIN_REPEAT_THRESHOLD` / `HIGH_SIGNAL_MAGNITUDE_THRESHOLD` | dashboard? | `2` / `2` / `0.7` (agent self-nudge thresholds in `wargame_ai`) |
| `APP_ENV` / `LOG_LEVEL` / `DB_ECHO` | dashboard? | `development` / `INFO` / `false` |

### Frontend build-time (Vite — baked at build, not runtime)
| Var | Default | Note |
|-----|---------|------|
| `VITE_API_BASE_URL` | `` (relative) | empty in prod = same-origin |
| `VITE_SWARM_API_URL` / `VITE_SWARM_WS_URL` | `/api/wargame` (same origin) | override only if the wargame is deployed separately |

> **`.env.example` is now complete** (Phase 2): it covers `APP_ENV`, `EMISSARY_AUTH_SECRET`,
> `EMISSARY_ADMIN_USERS`, `CORS_ORIGINS`, `EMISSARY_MOCK_DATA`, `RISK_FEED_MODE`, the optional
> data-source keys, and a wargame-backend section. This matrix and `.env.example` should agree;
> use either as the reference.

## First-deploy / production checklist

1. **Secrets in the Render dashboard:** `ANTHROPIC_API_KEY`, `EMISSARY_AUTH_SECRET` (strong
   random), Twilio + SendGrid keys, any data-source keys you want live.
2. **`APP_BASE_URL`** = the real deployed URL; **`CORS_ORIGINS`** = the frontend origin;
   **`EMISSARY_ADMIN_USERS`** = your admin username(s).
3. **pgvector:** `psql "$DATABASE_URL" -c "CREATE EXTENSION IF NOT EXISTS vector;"` once.
4. **Verify:** `GET /api/health` returns ok with no config issues; if `WARGAME_DEBUG_ERRORS=1`,
   check `/api/wargame-debug/lifespan` for subapp status.
5. Confirm `requirements.txt` is in sync with `pyproject.toml` (the `build-script` CI job guards this).
