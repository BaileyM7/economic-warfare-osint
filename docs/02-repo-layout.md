# 02 — Repo Layout

Directory-by-directory orientation. Use this to find *where* a thing lives; use
[03-backend-and-api.md](03-backend-and-api.md) for *how* the backend wires together.

## Top level

```
economic-warfare-osint/
├── src/                  # All Python application code (see below)
├── frontend/             # VENDORED (in-repo) React+Vite SPA → built to frontend/dist/, served by the backend
├── tests/                # pytest suite (unit + e2e + notifications)
├── templates/            # Jinja2 templates (email/report)
├── fixtures/             # Demo/seed data loaded when EMISSARY_MOCK_DATA=1
├── scripts/              # Utility scripts (e.g. check-swarm-connection.sh)
├── docs/                 # ← you are here (authoritative docs)
├── plans/                # Historical planning/handoff notes (being archived into docs/archive/)
├── data/                 # Runtime SQLite DB + disk cache (gitignored)
├── render.yaml           # Render deployment definition (services, env, cron, DB, Redis)
├── build.sh              # Render/CI build: pip install, build frontend (no submodules)
├── requirements.txt      # Pinned deps for pip (compiled from pyproject); used by build.sh
├── pyproject.toml        # Project metadata, deps (+ dev/wargame extras), ruff + pytest config
├── uv.lock               # uv lockfile (reproducible installs)
├── .python-version       # Pins the local uv interpreter (3.12.7)
├── .env.example          # Template for local .env (complete — mirrors the matrix in 06)
├── .pre-commit-config.yaml
├── README.md             # Quick start
├── plan.md               # Historical top-level plan (archive candidate)
└── clear_peers_cache.py  # One-off maintenance script (stray; see 08)
```

> **No git submodules.** `frontend/` is **vendored** (regular tracked files in this repo), and the
> wargame backend lives at `src/wargame_*` — there is no `swarm/` directory anymore. Their internals
> are documented in [07-submodules/](07-submodules/).

## `src/` — application code

### Top-level modules
| Path | Role |
|------|------|
| [src/api.py](../src/api.py) | **The FastAPI app — pure composition.** App creation, middleware, CORS, rate-limit, static/SPA serving, the `/ws/monitoring` socket + manager, lifespan/startup, `/api/health`, and the wargame mount. ~334 lines after the Phase-2 decomposition (the analysis/search endpoints moved into `src/routers/`). |
| [src/db.py](../src/db.py) | SQLite schema + queries (9 tables), `init_db()`, `seed_mock_data(force=…)` (seed-only-if-empty), `log_activity()`. |
| [src/auth.py](../src/auth.py) | HMAC bearer-token auth: `require_auth`, `require_admin`, admin-user parsing. |
| [src/analytics.py](../src/analytics.py) | `UsageTrackingMiddleware` → writes to the `usage_events` table. |
| [src/llm.py](../src/llm.py) | Claude client helper **plus** the shared `generate_narrative` / `generate_recommendations` helpers and the CoA system prompt. |
| [src/sanctions_impact.py](../src/sanctions_impact.py) | Deterministic stock-impact model from 13 historical sanction cases (no LLM). Powers `/api/sanctions-impact`. |
| [src/comparable_sourcer.py](../src/comparable_sourcer.py) | Finds comparable companies/cases for impact projection. |

### Packages (the 3 layers + dashboard)
| Path | Layer / role | Key files |
|------|--------------|-----------|
| [src/orchestrator/](../src/orchestrator/) | Layer 1 — agent | `main.py` (Orchestrator), `tool_registry.py`, `prompts.py`, `entity_resolver.py`, `person_search.py` |
| [src/tools/](../src/tools/) | Layer 2 — data | one subdir per domain (see [04](04-tools-and-data.md)) |
| [src/fusion/](../src/fusion/) | Layer 3 — output | `graph_builder.py`, `renderer.py` |
| [src/common/](../src/common/) | shared infra | `types.py`, `config.py`, `cache.py`, `http_client.py`, `rate_limit.py`, `sanitize.py`, plus the Phase-2 helpers: `graph_helpers.py` (node/truncate/canonical_lei/lei_resolve_node_id/ENTITY_COLORS), `screening_helpers.py` (`ofac_hit_matches_company_label`), `analyses.py` (the in-memory `_analyses` store, relocated out of `api.py`) |
| [src/routers/](../src/routers/) | dashboard **+ analysis/search** API | dashboard: `auth`, `admin`, `coa`, `briefings`, `monitoring`, `risk_feed`, `watchlist`, `notifications`, `_shared`. Phase-2 analysis/search routers (extracted from `api.py`): `orchestrator` (analyze/sync/{id}/tools), `entity` (entity-graph + resolve-entity), `person` (profile + search + network), `sector`, `risk` (entity-risk-report), `vessel`, `screening`, `sayari`, `sanctions_impact`, `followup` |
| [src/risk_feed/](../src/risk_feed/) | risk-feed enrichment | `enrich.py` (note: the *router* is `routers/risk_feed.py` — logic is split across two places, see [08](08-fragility-map.md)) |
| [src/notifications/](../src/notifications/) | SMS/email | `dispatcher`, `sms`, `email_digest`, `synthesis`, `caps`, `glossary`, `clients`, `stub_client*`, `scenarios` |

### Wargame (`src/wargame_*` — the canonical copy)
| Path | Role |
|------|------|
| [src/wargame_backend/](../src/wargame_backend/) | FastAPI subapp: `app/main.py`, `app/config.py`, `app/api/*`, `app/ws/*`, `app/db/*`, `alembic/` |
| [src/wargame_ai/](../src/wargame_ai/) | LangGraph sim engine: `agents/`, `sim/`, `memory/` |
| [src/wargame_shared/](../src/wargame_shared/) | Shared Pydantic schemas + YAML seeds (countries, Taiwan scenario) |

These are the **sole, canonical** wargame copy and are what actually runs. They originated as an
import-renamed copy of the now-removed `swarm/src/{backend,ai,shared}` submodule. See
[07-submodules/swarm-wargame.md](07-submodules/swarm-wargame.md) for that provenance.

## `src/tools/` — domain layout

Convention is `server.py` + `client.py` + `models.py` per domain, but it is **not uniform**:

| Domain | Files | Notes |
|--------|-------|-------|
| `sanctions/` | server, client, models, **delta.py** | OpenSanctions + OFAC |
| `corporate/` | server, client, models | OpenCorporates + GLEIF + ICIJ |
| `market/` | server, client (27 KB), models | **equity tool**: yfinance, Finnhub, SEC EDGAR |
| `markets/` | **feed.py only** | **different thing**: `build_markets_feed` for the risk feed. The `market`/`markets` name clash is a known sharp edge ([08](08-fragility-map.md)). |
| `trade/` | server, client, models | UN Comtrade, UNCTAD |
| `geopolitical/` | server, client, models | GDELT, ACLED |
| `economic/` | server, client, models | FRED, IMF, World Bank |
| `vessels/` | client, **geo.py**, **fixtures/** | no `server.py`/`models.py`; AIS + OpenSanctions vessel schema + curated fixtures |
| `sayari/` | server, client, models, **rest_client.py**, **rest_models.py** | premium entity resolution (optional) |
| `screening/` | **client.py only** | CSL aggregator (OFAC+BIS+EU+UN) |

## `tests/` — what's covered

```
tests/
├── conftest.py                       # clears secrets, sets demo creds, VCR config
├── test_opensanctions_parser.py
├── test_person_search.py
├── test_tools_import.py              # smoke: every tool imports
├── test_vessels_client.py
├── test_vessels_opensanctions.py
├── e2e/                              # test_input_validation, test_rate_limit, test_watchlist_e2e
└── notifications/                    # 11 files — by far the most-tested area
```

Coverage is **uneven**: notifications and vessels are well-tested; the orchestrator, fusion,
most tool domains, and the large `api.py` analysis endpoints have little-to-no direct
coverage. See [05-ci-cd.md](05-ci-cd.md) (how tests run) and [08](08-fragility-map.md) (gaps).

## Naming conventions

- **Routers** mount under `/api`; each is an `APIRouter` with a `prefix` and `tags` ([03](03-backend-and-api.md)).
- **Tools** return `ToolResponse` and cache via `src/common/cache.py`.
- **Config** is read from env, consolidated in `src/common/config.py` (see [01](01-architecture.md#configuration-model-consolidated-in-phase-2)); the wargame keeps its own pydantic-settings.
- **Wargame** code uses `wargame_backend` / `wargame_ai` / `wargame_shared` import roots (the
  now-removed `swarm` submodule used bare `app` / `ai` / `shared`).
