# Wargame Deployment Guide

The Emissary `/wargame` tab is a **ported frontend only** — it talks to a
separately-deployed instance of the [swarm](https://github.com/) backend
(the repo at `C:\Work\swarm`). This doc explains how to stand up swarm as
a microservice and point the Emissary frontend at it.

**If swarm is not deployed, demo mode still works** (client-side scripted
Taiwan 2027), but the preset Execute button and freeform Analyze button
will fail with a network error.

---

## Plan

1. **Choose a platform** — Fly.io (recommended), Docker on a VPS, or Render.
2. **Provision Postgres with pgvector + Redis** — these are required dependencies.
3. **Deploy the swarm backend container** — swarm's repo has Dockerfile +
   `fly.toml` + `docker-compose.prod.yml` ready to go.
4. **Configure secrets** — `ANTHROPIC_API_KEY`, `DATABASE_URL`, `REDIS_URL`,
   `CORS_ORIGINS`, agent model names.
5. **Run migrations and seed the data lake** — `alembic upgrade head` and
   the ingest runner.
6. **Wire Emissary's frontend to swarm** — set `VITE_SWARM_API_URL` and
   `VITE_SWARM_WS_URL` on the Emissary frontend build, redeploy Emissary.
7. **Smoke test** — hit `/healthz`, open `/wargame`, run the Taiwan 2027
   preset end-to-end.

Skip to [Option A](#option-a--flyio-recommended) if you just want to ship.

---

## Prerequisites

Regardless of platform:

- **ANTHROPIC_API_KEY** — billed to your Anthropic account. Swarm uses
  Claude Sonnet for country agents and Claude Opus for the arbiter.
  Budget ~$0.27 per 20-turn sim at Sonnet+Opus defaults.
- **Swarm source code** — clone `C:\Work\swarm` to a git remote your
  deploy platform can pull (GitHub/GitLab). Fly.io and Docker can also
  deploy from a local checkout.
- **Emissary frontend URL** — the origin that will call swarm. Needed
  for the `CORS_ORIGINS` allowlist on swarm's backend.

Swarm's minimum stack requirements (non-negotiable):

| Dependency | Why |
|---|---|
| PostgreSQL 15+ with **pgvector** extension | Agent memory semantic search (Voyage-3 embeddings) |
| Redis 5.2+ | World-state pub/sub during live sims; broadcast to WebSocket clients |
| Python 3.12 | FastAPI + LangGraph runtime |

---

## Option A — Fly.io (recommended)

**Why**: Swarm's repo already contains `fly.toml` pre-configured with the
correct health check, WebSocket support, and the Alembic release command.
Fly's managed Postgres ships with pgvector; Upstash Redis integration is
one command.

**Cost**: Postgres shared-cpu-1x + Redis 256MB + backend shared-cpu-1x ≈
$5–10/month at low usage.

### Commands

From inside `C:\Work\swarm`:

```bash
# Install flyctl if you don't have it: https://fly.io/docs/flyctl/install/
fly auth login

# Create the app (name can be anything; default in fly.toml is swarm-backend)
fly apps create swarm-backend

# Provision Postgres (pgvector is pre-installed on Fly's image)
fly postgres create --name swarm-db --region iad
fly postgres attach swarm-db --app swarm-backend
# ^^ automatically sets DATABASE_URL secret

# Provision Redis via Upstash (managed)
fly redis create --name swarm-redis --region iad
# Copy the Upstash redis URL from the output — then:
fly secrets set REDIS_URL="redis://default:<PASSWORD>@<HOST>.upstash.io:<PORT>" --app swarm-backend

# LLM + runtime secrets
fly secrets set \
  ANTHROPIC_API_KEY="sk-ant-..." \
  AGENT_MODEL="claude-sonnet-4-6" \
  ARBITER_MODEL="claude-opus-4-6" \
  AGENT_RUNNER_IMPL="langgraph" \
  CORS_ORIGINS="https://<your-emissary-frontend-url>" \
  --app swarm-backend

# Deploy (runs `alembic upgrade head` automatically per fly.toml)
fly deploy --app swarm-backend

# Seed the data lake (one-time; pulls GDELT + ACLED + World Bank)
fly ssh console --app swarm-backend --command \
  "python -m src.backend.ingest.runner --sources=gdelt,acled,worldbank"
```

Swarm is now live at `https://swarm-backend.fly.dev` (API) and
`wss://swarm-backend.fly.dev` (WebSocket).

### Fly.io notes

- **Sticky sessions for WebSocket**: `fly.toml` enables them by default.
  Don't disable — live sims keep a persistent WS connection.
- **Health checks**: Fly pings `/healthz` every 30s; unhealthy instances
  auto-restart.
- **Scale**: `fly scale count 1` is fine for demo use. If you need more
  concurrent sims, scale up; each sim holds a LangGraph state machine
  in memory.

---

## Option B — Docker Compose on a VPS

**Why**: Maximal control, single-machine cost floor (~$6/mo on DigitalOcean/
Hetzner). More ops work than Fly.

### Steps

On the VPS (Ubuntu 22.04+ with Docker installed):

```bash
git clone <your-swarm-fork> /opt/swarm
cd /opt/swarm
cp .env.example .env
# Edit .env:
#   ANTHROPIC_API_KEY=sk-ant-...
#   POSTGRES_PASSWORD=<strong random>
#   CORS_ORIGINS=https://<your-emissary-frontend-url>
#   AGENT_RUNNER_IMPL=langgraph
#   Remove/comment the NEXT_PUBLIC_* frontend vars (we don't deploy the frontend)

# Bring up backend + postgres + redis (skip frontend container)
docker compose -f docker-compose.prod.yml up -d postgres redis backend

# Run migrations
docker compose -f docker-compose.prod.yml exec backend alembic upgrade head

# Seed the data lake
docker compose -f docker-compose.prod.yml exec backend \
  python -m src.backend.ingest.runner --sources=gdelt,acled,worldbank

# Verify
curl http://localhost:8000/healthz   # → 200
```

Put Caddy or nginx in front for TLS + WebSocket upgrade:

```caddy
# /etc/caddy/Caddyfile
swarm.yourdomain.com {
    reverse_proxy localhost:8000
}
```

Caddy handles Let's Encrypt + WebSocket auto-upgrade automatically.

---

## Option C — Render

**Why**: Same dashboard as Emissary. But Render's managed Postgres
doesn't ship with pgvector — you'll need the Professional plan or
manually install the extension. Redis is a paid add-on.

### Services to create

1. **swarm-api**: Web Service, Docker runtime, pointing at
   `docker/backend.Dockerfile` in your swarm fork. Health check path
   `/healthz`. Use the free-tier web service only if you don't need
   sustained WebSocket — otherwise pay for a Starter plan ($7/mo) so
   the process doesn't sleep.
2. **swarm-postgres**: Render Postgres. After creation, connect via psql
   and run `CREATE EXTENSION IF NOT EXISTS vector;` (requires superuser,
   available on paid plans only).
3. **swarm-redis**: Render Key Value store. Starter plan $10/mo.

Set the same env vars as Fly (`ANTHROPIC_API_KEY`, `DATABASE_URL`,
`REDIS_URL`, `CORS_ORIGINS`, model names, `AGENT_RUNNER_IMPL=langgraph`).
Deploy, then use the Render shell for `alembic upgrade head` and the
ingest runner.

This path is only worth it if you strictly want one-dashboard ops.

---

## Emissary-side wire-up

Once swarm is reachable at `https://<swarm-host>`, set these on the
**Emissary frontend** build environment (Render dashboard → Emissary
frontend service → Environment):

```
VITE_SWARM_API_URL=https://<swarm-host>
VITE_SWARM_WS_URL=wss://<swarm-host>
```

**Rebuild + redeploy the Emissary frontend** after setting these vars.
Vite reads `VITE_*` at build time only — a config change requires a
new build.

For local dev where both swarm and Emissary run on your machine:

1. Start swarm on port **8001** (not 8000, which Emissary uses):
   ```bash
   cd C:\Work\swarm
   BACKEND_PORT=8001 docker compose up
   ```
2. Add to Emissary's `frontend/.env.local` (gitignored):
   ```
   VITE_SWARM_API_URL=http://localhost:8001
   VITE_SWARM_WS_URL=ws://localhost:8001
   ```
3. Restart Emissary's Vite dev server so it picks up `.env.local`.

---

## Smoke test

Run these from any machine that has curl:

```bash
SWARM=https://<swarm-host>

# 1. Health
curl -s "$SWARM/healthz"                       # → {"status":"ok"}

# 2. Country list (populated by migrations)
curl -s "$SWARM/api/countries" | head -c 200   # → JSON array, 10 countries

# 3. CORS preflight from Emissary's origin
curl -s -X OPTIONS "$SWARM/api/scenarios" \
  -H "Origin: https://<your-emissary-url>" \
  -H "Access-Control-Request-Method: POST" \
  -D - -o /dev/null
# → Expect: access-control-allow-origin: https://<your-emissary-url>

# 4. End-to-end scenario creation (this burns Anthropic credits)
curl -s -X POST "$SWARM/api/scenarios/extract-events" \
  -H "Content-Type: application/json" \
  -d '{"description":"China quarantines Taiwan in Q2 2027"}'
# → JSON with selected_countries + seed_events
```

Then from the Emissary UI as a logged-in user:

1. Navigate to `/wargame`.
2. In the ScenarioComposer sidebar, click the **China–Taiwan 2027**
   preset card.
3. Click **EXECUTE SIMULATION**.
4. Within ~5 seconds you should see: Turn counter flip to "Turn 1 / 3",
   globe arcs start animating, timeline fills with events, Decision Log
   auto-opens.

If any of those don't happen, check the browser console and the swarm
logs (`fly logs` or `docker logs swarm-backend`).

---

## Common failures and fixes

| Symptom | Likely cause | Fix |
|---|---|---|
| Wargame page loads, clicking Execute shows no feedback | CORS blocked | Add Emissary's origin to `CORS_ORIGINS` on swarm; redeploy |
| "Failed to fetch" on `/api/countries` | swarm URL misconfigured at build time | Rebuild Emissary frontend with correct `VITE_SWARM_API_URL` |
| Events appear for turn 0 then stop | WebSocket disconnected (no sticky sessions) | On Fly, check `fly.toml` has `[[services]]` with `protocol = "tcp"`. Elsewhere, ensure your load balancer supports WebSocket upgrade + sticky sessions |
| Sim starts but all countries no-op | `AGENT_RUNNER_IMPL` stuck on `null` (stub) | Set it to `langgraph` and redeploy |
| 500 errors calling `/api/scenarios/extract-events` | Missing `ANTHROPIC_API_KEY` | Set the secret; redeploy |
| Migrations fail with "pgvector not installed" | Postgres doesn't have the extension | On Render, `CREATE EXTENSION vector;` manually. On Fly/Docker this should be automatic |

---

## Cost watch

Swarm calls two LLMs per turn:

- **Agent decisions**: Claude Sonnet (one call per active country, ~10 countries in the Taiwan preset)
- **Arbiter**: Claude Opus (one call per turn)

Ballpark: **~$0.27 per 20-turn sim** at default settings. A 5-turn preset
is ~$0.07. If your boss demos 50 times, that's ~$3.50 — cheap.

If you want to cut the cost ~10×, set `AGENT_MODEL=claude-haiku-4-5-20251001`
on swarm. Quality drops noticeably for arbitration-heavy scenarios but is
usually fine for demo walkthroughs.

---

## Updating swarm later

Swarm is an independent repo. To ship a new version:

1. Pull changes in swarm's repo.
2. `fly deploy --app swarm-backend` (or `docker compose up -d --build backend` on the VPS).
3. No Emissary redeploy needed unless swarm's API shape changed (check
   `src/wargame/lib/types/` vs. swarm's current schemas).

---

## Related files

- [.env.example](../.env.example) — documents the `VITE_SWARM_*` vars
- [frontend/src/wargame/](../frontend/src/wargame/) — ported swarm UI
- [plans/](../plans/) — historical integration plan
