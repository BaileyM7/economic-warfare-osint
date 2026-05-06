# Emissary — Repo-Level Context

**This file loads automatically into every Claude Code session.** It captures the
non-obvious things you need to know before touching this repo: the branch flow,
the deploy mechanism, the security gates, and the policy decisions that aren't
visible from reading code alone. Subdirectory `CLAUDE.md` files cover their
local context (see [src/CLAUDE.md](src/CLAUDE.md), [src/common/CLAUDE.md](src/common/CLAUDE.md),
[src/routers/CLAUDE.md](src/routers/CLAUDE.md), [tests/CLAUDE.md](tests/CLAUDE.md)).

---

## What this is

Emissary is a customer-facing demo POC of a multi-agent OSINT system for
economic warfare scenario analysis. Natural-language questions in →
structured impact assessments out, with citations and confidence scores.

It substitutes free data sources (OFAC, OpenSanctions, GDELT, yfinance, FRED,
ICIJ Offshore Leaks) for paid enterprise sources (Sayari, Kharon, Refinitiv).
**The only required paid service is the Anthropic API key** — everything else
either has a free tier or is unauthenticated.

The codebase has a three-layer architecture detailed in [src/CLAUDE.md](src/CLAUDE.md):
orchestrator (Claude API) → MCP tool agents (one per data domain) → fusion
engine (output rendering).

---

## Branch flow — read before opening any PR

This repo follows a **two-branch staging-and-prod model**:

| Branch | Role | Auto-deploy? | Direct push? |
|---|---|---|---|
| `develop` | Local-staging | No (manual `git checkout develop && uvicorn ...` to test) | Blocked by branch protection |
| `emissary-v1` | Production | **Yes** — Render auto-pulls and rebuilds on every push | Blocked by branch protection |
| `master` | Historical default | No | Blocked |

### The standard feature flow

```
feature/xyz  ──PR──▶  develop  ──manual test──▶  PR  ──▶  emissary-v1  ──▶  Render rebuild
   │                     │                        │            │
   │                     CI runs (5 jobs)         CI runs again │
   │                                                            └─ live in ~3 min
   └─ created from latest develop tip
```

1. Branch from latest `develop` (`git checkout develop && git pull && git checkout -b feature/xyz`).
2. Push your branch, open PR into `develop`. CI runs (`lint`, `test`, `frontend-build`, `build-script`, `secret-scan`).
3. CI green → merge into `develop`.
4. Pull `develop`, run locally (`uvicorn src.api:app --reload`), poke at the change in a real browser. This is your staging gate.
5. Open PR `develop → emissary-v1`. CI runs again on the merge candidate.
6. CI green → merge. Render auto-pulls `emissary-v1` and rebuilds. Live in ~3 min.

### Hotfix policy (pragmatic, not strict)

When prod is broken (failed deploy, security incident, customer-blocking bug),
PR straight from a feature branch → `emissary-v1`. Skip develop. Speed matters
more than the ceremony of two PRs. **After the hotfix lands, open a follow-up
PR `emissary-v1 → develop` to keep the branches aligned** — otherwise develop
falls behind and the next feature PR carries an enormous diff.

This is what we did for PR #2 (the `SUGGESTED_ENTITIES` import fix that was
breaking prod startup). It's the only sanctioned exception to the standard flow.

### What CI gates

Branch protection on `develop` and `emissary-v1` requires all 5 jobs to pass
before the merge button enables:

| Job | Catches | ~Time |
|---|---|---|
| `lint` (ruff) | Style, unused imports, undefined names | 10s |
| `test` (pytest) | Logic regressions, all 60 tests in [tests/](tests/) | 1–2 min |
| `frontend-build` (vite) | TypeScript errors, missing deps | 2 min |
| `build-script` | Render deploy failures (full `bash build.sh`) | 3–5 min |
| `secret-scan` (gitleaks) | Committed `.env`, API keys in diffs | 30s |

Direct pushes to `develop`/`emissary-v1` are blocked. Force-pushes are blocked.
Approval requirement is currently OFF (solo dev) — re-enable when adding
collaborators by editing the rule in GitHub Settings → Branches.

---

## Deploy mechanism (Render)

[render.yaml](render.yaml) defines the prod stack:
- `emissary` web service (Python, Starter plan) — the FastAPI app
- `swarm-redis` Key Value (Starter, $10/mo) — used by rate limiter, no fallback
  needed in code (slowapi falls back to in-memory if `REDIS_URL` is unset)
- `swarm-db` managed Postgres — only used by the embedded wargame subapp,
  not by the main Emissary service

Render auto-pulls `emissary-v1` on every push. The build runs [build.sh](build.sh)
(submodule init → pip install → frontend build via npm + vite). If build.sh
fails, the previous deploy stays live.

**Critical env vars (set manually in Render dashboard, not in repo):**
- `ANTHROPIC_API_KEY` — required, paid
- `CORS_ORIGINS` — must include your prod frontend URL, comma-separated. The
  code refuses `*` at startup (modern browsers reject `*` + `allow_credentials=True`
  anyway). Without this, the deploy boots but the frontend fails CORS preflight.
- `EMISSARY_AUTH_SECRET` — HMAC secret for auth tokens. Default `"dev-secret-change-me"`
  is dev-only.
- `EMISSARY_DEMO_PASSWORD` — change from default `"demo"`. Public access
  control is currently weak; the demo password is the only thing between an
  attacker and your Anthropic budget (rate-limited at 30 calls/day/user, but
  not zero).
- `FRED_API_KEY`, `ACLED_*`, `SAYARI_*` — optional, free with registration

---

## Local development

```bash
# First time only:
git submodule update --init --recursive
uv sync --extra dev
uv run pre-commit install --hook-type pre-commit --hook-type pre-push

# Daily:
uvicorn src.api:app --reload          # backend at http://localhost:8000
cd frontend && npm run dev            # frontend at http://localhost:5173
```

The pre-commit hooks run ruff lint + format on every commit, and `pytest -x`
on every push. Don't bypass with `--no-verify` unless prod is on fire.

If `uvicorn` fails to import the app at startup, check whether you have all
runtime deps installed (`uv sync` should cover this). If a router import
errors, that same import will fail on Render — fix it before pushing.

---

## What NOT to do

These are the patterns that have caused real incidents in this repo. Future-Claude:
treat these as hard rules, not preferences.

1. **Never commit `.env`.** It's in `.gitignore` (lines 2-5) and protected by
   the gitleaks CI job. If you find yourself reaching for `git add .env`,
   stop.
2. **Never call Anthropic from a router without `@limiter.limit(...)` and
   `sanitize_for_llm(...)` on the user content.** See [src/routers/CLAUDE.md](src/routers/CLAUDE.md)
   for the standard endpoint pattern.
3. **Never set `CORS_ORIGINS=*` in prod.** The code rejects this at startup
   for good reason — `*` + `allow_credentials=True` is silently broken under
   modern browsers.
4. **Never PR straight to `emissary-v1` for a feature change.** Hotfixes
   only. See branch flow above.
5. **Never record VCR cassettes against the prod Anthropic key without
   running `python scripts/redact-cassettes.py` before commit.** The
   pre-commit hook catches this if cassettes are staged, but only for known
   `.env` values. Secrets loaded from elsewhere (shell exports, `~/.aws/credentials`)
   silently leak.
6. **Don't rely on `master` for anything.** It's a historical default. The
   real prod branch is `emissary-v1`.

---

## Where to look first when something breaks

| Symptom | Look at |
|---|---|
| Render deploy fails | Render dashboard → Events tab. If build.sh failed, check CI's `build-script` job for the same SHA — it should have caught it. |
| Frontend can't reach API in prod | `CORS_ORIGINS` env var on Render. Browser console will say "blocked by CORS policy". |
| `/api/coa/generate` returns 429 | Rate limit working as designed. Check `redis-cli -u $REDIS_URL KEYS "LIMITER/*"` to see counters. |
| Pytest passes locally but fails CI | Submodule not checked out (CI uses `submodules: recursive` for some jobs). Or `ANTHROPIC_API_KEY` differs between envs. |
| All 18 E2E tests error at fixture setup | An import in `src/routers/` fails. The app can't boot. This was PR #2's bug. |
