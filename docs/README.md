# Emissary / Economic Warfare OSINT — Documentation

This is the **authoritative, current** documentation set for the system. It covers how
the repo is laid out, how a request flows through the code, how CI gates merges, and how
the app runs in production on Render — plus the frontend SPA and the wargame backend.

> **Status:** Written 2026-06 against branch `emissary-v1`. These docs are meant to be
> kept current. Point-in-time planning/handoff notes have been moved to [archive/](archive/)
> — read them for history, not for how things work today.

## 60-second overview

**Emissary** is a natural-language **economic-warfare intelligence tool**. An analyst asks a
question ("What happens if we sanction Fujian Jinhua?"); the system gathers data from ~15
free public OSINT sources, fuses it with Claude, and returns a structured impact assessment
with an entity graph. Around that engine sits a dashboard ("Emissary") with a risk feed,
per-user watchlists, courses of action (COAs), briefings, monitoring, and notifications. A
separate **wargame** tab runs a multi-agent geopolitical simulation.

It is a **Python monorepo** (FastAPI + Claude) with **no git submodules**:
- `frontend/` — a React + Vite SPA, **vendored** into this repo as regular tracked files (its
  upstream was `github.com/deveshkumars/economicgamingv1`); built and served as static files by the backend.
- The wargame backend lives in this repo under `src/wargame_*` and is what actually runs (the
  former standalone `swarm/` submodule was removed). See
  [07-submodules/swarm-wargame.md](07-submodules/swarm-wargame.md).

One web process serves everything: the API, the static SPA, and (when `WARGAME_ENABLED=1`)
the wargame backend mounted at `/api/wargame`.

## The docs

| # | Doc | What it covers |
|---|-----|----------------|
| 01 | [architecture.md](01-architecture.md) | The two product surfaces, the 3-layer analysis engine, the dashboard layer, request lifecycle, core data models |
| 02 | [repo-layout.md](02-repo-layout.md) | Directory-by-directory orientation; where each feature lives; naming conventions |
| 03 | [backend-and-api.md](03-backend-and-api.md) | FastAPI structure (`api.py` + routers), **full endpoint catalog**, auth, rate limiting, middleware, SQLite DB, background tasks |
| 04 | [tools-and-data.md](04-tools-and-data.md) | The Layer-2 tool domains, every external data source, keys, fixtures, caching |
| 05 | [ci-cd.md](05-ci-cd.md) | The 5 CI jobs, `build.sh`, pre-commit, VCR cassettes, branch/PR workflow |
| 06 | [deployment-render.md](06-deployment-render.md) | `render.yaml` services, **full env-var matrix**, build/start, cron digest, Postgres/Redis/pgvector |
| 07 | [submodules/frontend.md](07-submodules/frontend.md) | The React SPA (vendored): stack, routing, pages, API client, how it's served |
| 07 | [submodules/swarm-wargame.md](07-submodules/swarm-wargame.md) | The embedded `src/wargame_*` wargame (provenance: the now-removed `swarm` submodule); sim engine; WS/REST contract |
| 08 | [fragility-map.md](08-fragility-map.md) | **Coupling & fragility inventory** — the bridge to the reliability (Phase 2) work |

**Also kept (operational guides, not superseded):**
- [notifications-setup.md](notifications-setup.md) — Twilio/SendGrid setup and the weekly digest.
- [wargame-deployment.md](wargame-deployment.md) — standalone swarm deployment notes.

## Where do I find X?

| I want to… | Start here |
|------------|-----------|
| Understand the big picture | [01-architecture.md](01-architecture.md) |
| Find where a feature's code lives | [02-repo-layout.md](02-repo-layout.md) |
| Debug an endpoint returning 4xx/5xx | [03-backend-and-api.md](03-backend-and-api.md) (endpoint catalog → which file) |
| Add or fix a data source | [04-tools-and-data.md](04-tools-and-data.md) + `src/tools/<domain>/` |
| Understand why CI failed | [05-ci-cd.md](05-ci-cd.md) |
| Fix a deploy / set an env var / a key | [06-deployment-render.md](06-deployment-render.md) |
| Work on the UI | [07-submodules/frontend.md](07-submodules/frontend.md) |
| Work on the wargame | [07-submodules/swarm-wargame.md](07-submodules/swarm-wargame.md) |
| Know what's risky to change | [08-fragility-map.md](08-fragility-map.md) |

## Keeping these docs current

- The **source of truth is the code.** When you change routes, env vars, or the data-source
  list, update the matching doc in the same PR (03 for endpoints, 06 for env vars, 04 for sources).
- Project quick-reference for humans/agents lives in the root [CLAUDE.md](../CLAUDE.md); it points here.
- When something in [08-fragility-map.md](08-fragility-map.md) gets fixed, strike it through with a
  short note rather than silently deleting — it records *why* the code is shaped the way it is.
