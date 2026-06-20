# 08 — Coupling & Fragility Map

The bridge from "understand the system" (Phase 1) to "make it sturdier" (Phase 2). Each entry is
*what's brittle → what it can break → a rough decoupling direction*. **This is a map, not an
implementation plan** — Phase 2 will scope fixes using it.

Severity is about blast radius × likelihood, not effort.

## High severity

### ~~F1 — `src/api.py` is a 3,350-line god-file~~
**RESOLVED (Phase 2):** `api.py` was decomposed from ~3,350 lines to **~334 lines of pure app
composition** (FastAPI construction, middleware, CORS, rate-limit, static/SPA serving, the
`/ws/monitoring` socket + manager, lifespan/startup, `/api/health`, the catch-all, and the
wargame mount). The ~25 analysis/search endpoints moved OUT into dedicated routers in
`src/routers/` (`orchestrator`, `entity`, `person`, `sector`, `risk`, `vessel`, `screening`,
`sayari`, `sanctions_impact`, `followup`) — same paths, same `require_auth` at include. Shared
logic landed in helper modules: `src/llm.py` (now also `generate_narrative` /
`generate_recommendations` + the CoA system prompt), `src/common/graph_helpers.py`,
`src/common/screening_helpers.py`, and `src/common/analyses.py`.
- **Brittle (historical):** app construction, middleware, static serving, the WebSocket manager,
  in-memory stores, **and ~25 analysis/search endpoints** all lived in one module
  ([src/api.py](../src/api.py)). CODEOWNERS already flags it as security-sensitive.
- **Breaks:** any change risked an unrelated endpoint; merge conflicts concentrated here; hard to
  test in isolation; reviewers couldn't reason about blast radius.
- **Direction:** ~~extract the analysis/search endpoints into `src/routers/` modules (the dashboard
  endpoints already live there — follow that pattern), pull the app-factory + static serving +
  WS manager into their own modules, leave `api.py` as thin composition.~~ Done.

### ~~F2 — Embedded wargame (`src/wargame_*`) is a manual fork of the `swarm/` submodule~~
**RESOLVED (Phase 2):** swarm submodule removed; `src/wargame_*` is the single source.
- **Brittle:** `src/wargame_backend|ai|shared` is an import-renamed copy of `swarm/src/*` with two
  Render-only patches. No automated sync ([07-submodules/swarm-wargame.md](07-submodules/swarm-wargame.md)).
- **Breaks:** a fix in one copy silently misses the other; the next "re-sync" can clobber the
  embedded patches (in-process Alembic, Postgres DSN normalize).
- **Direction:** ~~pick one source of truth. Either (a) make the submodule a true dependency and
  generate the rename at build time, or (b) delete the submodule and treat `src/wargame_*` as
  canonical (documenting the upstream provenance). Until then: a checklist + a diff test that
  fails when the two diverge.~~ Resolved via option (b): the `swarm/` submodule was deleted and
  `src/wargame_*` is now canonical, with the upstream provenance documented in
  [07-submodules/swarm-wargame.md](07-submodules/swarm-wargame.md).

### F3 — App state is in-memory and/or on an ephemeral disk — **PARTIAL (Phase 2)**
**PARTIAL (Phase 2):** the data-loss vectors are fixed — `seed_mock_data()` is now
**seed-only-if-empty** (with a `force=` param) so deploys can't wipe analyst-created
COAs/briefings, and [render.yaml](../render.yaml) now declares a **1 GB persistent disk** at
`/opt/render/project/src/data` so the SQLite DB survives deploys. The `_analyses` store was
relocated to [src/common/analyses.py](../src/common/analyses.py) — but it is **still in-memory
and process-local**, so the durability / multi-instance concern for live analyses partly remains.
- **Brittle:** `_analyses` (analysis results) and `_ws_manager` are **process-local** — lost on
  restart, not shared across workers/instances. *(The SQLite-DB ephemerality is now resolved by
  the persistent disk above.)*
- **Breaks:** the in-memory analysis store silently 404s after a restart mid-analysis; still can't
  run >1 web instance without a shared store.
- **Direction:** move `_analyses` to a shared store (DB/Redis) or accept single-instance and
  document it explicitly.

### ~~F4 — Configuration is split three ways with no single source of truth~~
**RESOLVED (Phase 2):** app config is consolidated in
[common/config.py](../src/common/config.py) — the previously-scattered `os.getenv` sites
(`CORS_ORIGINS`, `WARGAME_ENABLED`, `EMISSARY_MOCK_DATA`, `RISK_FEED_MODE`, demo creds,
`EMISSARY_AUTH_SECRET`, `EMISSARY_ADMIN_USERS`) now read from `config`. There's an `APP_ENV`
(development|staging|production) + `is_production` property, and `config.validate()` now fails
loudly on prod misconfig (e.g. `EMISSARY_AUTH_SECRET` left at the dev default, empty
`CORS_ORIGINS`). `.env.example` is now **complete** (see [06](06-deployment-render.md)). The
wargame keeps its own pydantic-settings by design.
- **Brittle (historical):** the `Config` dataclass, ~11 scattered `os.getenv` sites, and the
  wargame's own pydantic-settings; an incomplete `.env.example`.
- **Breaks:** new settings got inconsistent defaults; prod misconfig was easy and silent.
- **Direction:** ~~consolidate into one typed settings object, make `.env.example` authoritative,
  and `config.validate()` the prod-required ones at startup.~~ Done.

### ~~F5 — Thin tests exactly where churn is highest~~
**RESOLVED / mitigated (Phase 2):** a safety net was added *before* the F1 decomposition — a
**route-inventory snapshot** + **auth-gate contract** (`tests/test_api_contract.py`, pinning the
public route surface and that protected endpoints reject unauthenticated requests) plus
**surfacing unit tests** for the entity/scoring logic (`tests/test_sanctions_integration.py`,
`tests/test_vessels_lookup.py`), and reliability tests (`tests/test_seed_idempotent.py`,
`tests/test_rate_limit_storage.py`). Broad orchestrator/fusion coverage is still thin, but the
high-churn refactor path is now guarded.
- **Brittle (historical):** notifications + vessels were well-covered; the orchestrator, fusion,
  most tool domains, and the large `api.py` analysis endpoints had little/no direct tests.
- **Breaks:** regressions in the most-edited, highest-value path went unnoticed until a demo.
- **Direction:** ~~add VCR-backed tests around the surfacing/scoring functions and the top analysis
  endpoints *before* refactoring F1.~~ Done — they became the safety net for the decomposition.

### ~~F16 — Rate-limiter 500'd every request on a Redis blip~~
**FOUND + FIXED (Phase 2):** when `REDIS_URL` was set but Redis was momentarily unreachable,
slowapi's middleware path mishandled the `ConnectionError` and **500'd every request** — a real
outage bug. [src/common/rate_limit.py](../src/common/rate_limit.py) now **probes Redis at
startup** and falls back to in-memory limiting if it's unreachable, and sets slowapi
`swallow_errors=True` so the app **fails open** rather than down. Regression-guarded by
`tests/test_rate_limit_storage.py`.

## Medium severity

### F6 — `market/` vs `markets/` name collision
`src/tools/market/` (equity client tool) and `src/tools/markets/` (a single `feed.py` for the
risk feed) are different things one import away from each other. **Breaks:** easy to import the
wrong one. **Direction:** rename `markets/` (e.g. fold `feed.py` into `risk_feed/` or rename to
`market_feed/`).

### ~~F7 — Duplicate follow-up endpoints~~
**RESOLVED (Phase 2):** the duplicate `POST /api/follow-up` was removed; only `POST /api/followup`
remains (the one the frontend uses), now living in [routers/followup.py](../src/routers/followup.py).
- **Brittle (historical):** `POST /api/follow-up` and `POST /api/followup` both took
  `FollowUpRequest` with overlapping intent — unclear which was canonical; dead-code risk.
- **Direction:** ~~keep one, redirect/remove the other, update the frontend.~~ Done.

### F8 — Endpoints bypass the orchestrator and import tool clients directly
Many `api.py` endpoints do `from src.tools.market.client import YFinanceClient` etc. rather than
going through `ToolRegistry`. **Breaks:** a client signature change ripples into both the agent
path and several endpoints with no single seam. **Direction:** funnel data access through a
service/registry layer; keep clients private to their domain.

### F9 — Risk-feed logic is split across three places
`src/risk_feed/enrich.py`, `src/routers/risk_feed.py`, and `src/tools/markets/feed.py` together
implement one feature. **Breaks:** low cohesion; changes require touching scattered files.
**Direction:** consolidate into one `risk_feed` package with a clear router/service/data split.

### F10 — Inconsistent tool-package convention
`vessels/` (no `server.py`/`models.py`), `markets/` (feed only), `screening/` (client only),
plus extras in `sanctions/` and `sayari/`, deviate from the documented `server/client/models`
pattern. **Breaks:** `ToolRegistry`'s lazy-import assumptions are uneven; onboarding friction.
**Direction:** either conform the outliers or document them as intentional and teach the registry about them.

### F11 — `requirements.txt` ↔ `pyproject.toml` drift — **PARTIAL (Phase 2)**
**PARTIAL (Phase 2):** a `.python-version` (`3.12.7`) was added so local `uv` uses the right
interpreter — but the full `uv.lock` ↔ `requirements.txt` version reconciliation is **still a
deferred follow-up**.
Local dev uses `uv`; build/deploy uses `pip install -r requirements.txt`. **Breaks:** a dep added
to `pyproject` but not recompiled into `requirements.txt` passes locally and fails on Render.
**Direction:** a single regenerate step (and the `build-script` CI job already partly guards it).

## Low severity / hygiene

- **F12 — Stale guidance files.** `plans/CLAUDE.md` (says Python 3.14, wrong layout) and
  `src/tools/CLAUDE.md` (missing `vessels`/`sayari`/`screening`/`markets`) still mislead
  contributors. *(The previously-incomplete `.env.example` is now complete — Phase 2.)*
- **F13 — Stray root scripts.** `clear_peers_cache.py` at repo root signals ad-hoc maintenance;
  fold into `scripts/` or a make target.
- **F14 — Demo-grade auth.** Single demo user, HMAC secret with a weak default, admin via an env
  CSV. Fine for a demo; needs hardening before real multi-user use.
- **F15 — Wargame double-prefix paths.** `/api/wargame/api/...` is awkward (mount prefix + router
  prefix). Cosmetic, but confusing; consider dropping the inner `/api` when embedding.

## Suggested Phase-2 sequencing

1. ~~**Safety net first:** F5 (tests) + F4 (config consolidation + `validate()` on boot).~~ Done.
2. **Durability:** F3 — *partial*: data-loss seeding fixed + persistent disk declared; `_analyses`
   relocated but still in-memory/process-local (move to a shared store remains).
3. ~~**Decompose:** F1 (`api.py` → routers) — now backed by F5's tests.~~ Done (~334-line `api.py`).
4. **De-dup / cohesion:** ~~F7~~ (done), F6, F9, F8 still open.
5. ~~**Wargame source-of-truth:** F2.~~ Done — swarm submodule removed; `src/wargame_*` is canonical.
6. **Hygiene:** F10–F15 opportunistically; F11 partial (`.python-version` added, lock reconciliation
   deferred). Also fixed opportunistically: F16 (rate-limiter fail-open).
