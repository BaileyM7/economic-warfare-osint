# CLAUDE.md — Emissary / Economic Warfare OSINT

Project quick-reference for humans and agents. **The full, authoritative docs live in
[docs/](docs/README.md) — read those for anything non-trivial.** This file is a pointer + cheatsheet.

## What this is

A natural-language **economic-warfare OSINT tool**: an analyst asks a question, a Claude
**orchestrator** pulls data from ~15 free public sources in parallel, and a **fusion** layer
returns a structured impact assessment with an entity graph. Around it sits the **Emissary
dashboard** (risk feed, watchlists, COAs, briefings, monitoring, notifications) and an optional
**wargame** simulation. One FastAPI process serves the API, the static React SPA, and (when
`WARGAME_ENABLED=1`) the wargame subapp at `/api/wargame`.

Python 3.12 · FastAPI + Uvicorn · Anthropic Claude · SQLite (app) + Postgres/Redis (wargame).
No git submodules: `frontend/` is vendored into this repo and the wargame lives at `src/wargame_*`.

## Start here

| Need | Doc |
|------|-----|
| Big picture | [docs/01-architecture.md](docs/01-architecture.md) |
| Where code lives | [docs/02-repo-layout.md](docs/02-repo-layout.md) |
| Endpoints / backend | [docs/03-backend-and-api.md](docs/03-backend-and-api.md) |
| Data sources / keys | [docs/04-tools-and-data.md](docs/04-tools-and-data.md) |
| CI / tests | [docs/05-ci-cd.md](docs/05-ci-cd.md) |
| Deploy / env vars | [docs/06-deployment-render.md](docs/06-deployment-render.md) |
| Frontend / wargame | [docs/07-submodules/](docs/07-submodules/) |
| **What's risky to change** | [docs/08-fragility-map.md](docs/08-fragility-map.md) |

## Commands

```bash
# Setup (local dev uses uv; CI/Render use pip + requirements.txt — keep them in sync)
uv sync --extra dev                  # add --extra wargame for the simulation backend
# No git submodules: frontend/ is vendored in-repo; the wargame lives at src/wargame_*

# Run
uv run uvicorn src.api:app --reload --host 0.0.0.0 --port 8000   # web (primary)
uv run python -m src.orchestrator.main "What happens if we sanction Fujian Jinhua?"  # CLI

# Quality
uv run ruff check . && uv run ruff format --check .
uv run pytest tests/                 # VCR replay-only; no secrets needed
```

## Conventions & gotchas

- **3 layers:** `orchestrator/` (agent) → `tools/<domain>/` (data, return `ToolResponse`) →
  `fusion/` (render). Shared models in [src/common/types.py](src/common/types.py).
- **Routers** live in `src/routers/`; the analysis/search endpoints now live there too. After
  the Phase-2 decomposition, [src/api.py](src/api.py) is ~334 lines of pure app composition
  (no longer the old 3,350-line god-file — see [docs/08](docs/08-fragility-map.md)).
- **Config is consolidated** in [src/common/config.py](src/common/config.py): an `APP_ENV`
  (development|staging|production) with an `is_production` property and a `validate()` that
  fails loudly on prod misconfig. The wargame keeps its own pydantic-settings by design.
  `.env.example` is now complete; the authoritative env list is [docs/06](docs/06-deployment-render.md).
- **Wargame:** `src/wargame_*` is now the **only** copy (the `swarm/` submodule was removed); it's
  what runs, mounted at `/api/wargame` ([docs/07-submodules/swarm-wargame.md](docs/07-submodules/swarm-wargame.md)).
- **`market/` ≠ `markets/`** is still a known sharp edge; the `/api/follow-up` vs `/api/followup`
  duplicate is **resolved** (the dup was removed; only `/api/followup` remains) ([docs/08](docs/08-fragility-map.md)).
- **Branch/deploy:** PR into `emissary-v1`; 5 CI checks + 1 review; Render auto-deploys on merge.
- When you change endpoints, env vars, or data sources, **update the matching doc in the same PR**.
