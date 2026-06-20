# 05 — CI/CD

How code gets from a branch to production: the GitHub Actions gates, local pre-commit hooks,
the test strategy, and the branch → Render auto-deploy flow.

## The pipeline at a glance

```
local edit → pre-commit (ruff, redact cassettes) → push
   → pre-push hook (pytest -x)
   → PR to emissary-v1
   → GitHub Actions: lint · test · frontend-build · build-script · secret-scan   (all must pass)
   → 1 approving review (CODEOWNERS = @BaileyM7)
   → merge to emissary-v1
   → Render auto-deploys (build.sh → uvicorn)
```

## GitHub Actions ([.github/workflows/ci.yml](../.github/workflows/ci.yml))

**Triggers:** PRs targeting and pushes to `develop`, `emissary-v1`, `master`. (`develop` is
local-staging; `emissary-v1` is the prod auto-deploy branch; `master` is a backup.) Superseded
runs on the same ref are auto-cancelled (`concurrency … cancel-in-progress`).

**Five gating jobs** — all must pass before merge (enforced by branch-protection rules set in the
GitHub UI):

| Job | Command | ~Time | Catches |
|-----|---------|------|---------|
| **lint** | `ruff check .` + `ruff format --check .` | ~10s | style/lint drift from machines without pre-commit |
| **test** | `pip install -e ".[dev]"` → `pytest tests/ --cov=src` | 1–2m | logic regressions (VCR replay, no secrets) |
| **frontend-build** | `npm ci && npm run build` in `frontend/` (in-repo, no submodule checkout) | ~2m | TypeScript / Vite build failures |
| **build-script** | `bash build.sh` end-to-end | 3–5m | `requirements.txt` drift / deploy-time breakage |
| **secret-scan** | `gitleaks` over full history (`fetch-depth: 0`) | ~30s | committed `.env`/keys |

`build-script` is the most valuable gate: it runs the **exact** Render build (`build.sh`) with the
same Python (3.12.7) and Node (20.11.0), so deploy failures are caught in CI, not in production.

## Build script ([build.sh](../build.sh))

The single source of truth for "how the app is built" — used identically by CI's `build-script`
job and by Render:

```bash
set -e
pip install --upgrade pip
pip install -r requirements.txt            # NOTE: pip + requirements.txt, not uv, in build/deploy
cd frontend && npm ci && npx vite build && cd ..   # → frontend/dist/ (frontend is in-repo, no submodules)
```

> Local dev uses **uv** (`uv sync`); CI/Render use **pip + requirements.txt**. Keep
> `requirements.txt` in sync with `pyproject.toml` or `build-script`/deploy will drift from local.

## Linting & formatting (ruff)

Config in [pyproject.toml](../pyproject.toml) `[tool.ruff]`:
- `line-length = 100`, `target-version = "py312"`.
- `select = ["E", "F", "W"]`, `ignore = ["E501"]` — deliberately a **minimal** rule set (real
  bugs, not subjective style) so ruff upgrades don't introduce hundreds of new errors.
- `extend-exclude = ["swarm", "frontend", ".venv", "data"]` — `frontend/` is vendored JS/TS (linted
  by its own ESLint/Prettier, not ruff); the `swarm` entry is a leftover from the now-removed swarm
  submodule and is harmless (no such directory exists). The live wargame code under `src/wargame_*`
  **is** linted by ruff here.

## Pre-commit ([.pre-commit-config.yaml](../.pre-commit-config.yaml))

Install once: `uv sync --extra dev && uv run pre-commit install`.

- **ruff** (`--fix`) + **ruff-format** (pinned `v0.15.0` to match CI).
- Standard hygiene: trailing-whitespace, end-of-file, check-yaml/toml, merge-conflict,
  large-files (`--maxkb=1000`).
- **redact-cassettes** (local): runs `scripts/redact-cassettes.py` on staged
  `tests/cassettes/**/*.yaml` — strips secrets before they're committed.
- **pytest-pre-push** (local, `pre-push` stage): `pytest -x --no-header` — a failing test blocks
  the push before it reaches CI. Bypass only in emergencies with `git commit --no-verify`.

## Test strategy ([tests/conftest.py](../tests/conftest.py))

- Boots the **real FastAPI app in-process** via `TestClient`, against a **throwaway temp SQLite
  DB** (`src.db.DB_PATH` is patched before import; the DB is wiped between tests).
- **No real network**: `ANTHROPIC_API_KEY` is cleared, `REDIS_URL` removed (in-memory rate
  limiter), auth secret + demo creds pinned. Endpoints needing an LLM return 503 unless a cassette
  supplies the response.
- **VCR** (`vcrpy`): default `record_mode="none"` (replay-only; a request with no cassette fails).
  Sensitive headers/query-params are filtered; cassettes live in `tests/<area>/cassettes/<module>/`.
- Fixtures: `app_client`, `auth_token`, `auth_headers` (logs in as the demo user).

**Recording a new cassette** (manual, local, burns credits):
```bash
VCR_RECORD_MODE=once pytest tests/e2e/test_foo.py   # hits real services
python scripts/redact-cassettes.py                   # scrub secrets
pytest tests/e2e/test_foo.py                          # verify replay
git add tests/cassettes/ tests/e2e/test_foo.py && git commit
```

Coverage is **informational, not a gate** — and is uneven (notifications/vessels are well-tested;
the orchestrator, fusion, most tools, and the large `api.py` endpoints are thin). See [08](08-fragility-map.md).

## Review & ownership ([.github/CODEOWNERS](../.github/CODEOWNERS))

Default owner `@BaileyM7` for everything; security-sensitive paths
(`src/auth.py`, `src/common/rate_limit.py`, `src/common/sanitize.py`, `src/api.py`,
`.github/workflows/`, `.pre-commit-config.yaml`) auto-request his review. There's a
[PR template](../.github/pull_request_template.md) with a pre-merge checklist.

## Branch & deploy flow

- Feature branches → PR into **`emissary-v1`**.
- All 5 checks + 1 review → merge.
- Render watches `emissary-v1` and **auto-deploys** on merge (runs `build.sh`, then
  `uvicorn src.api:app`). There is no manual deploy step. Details in
  [06-deployment-render.md](06-deployment-render.md).
- Recent history is a run of `fix(...)` PRs (#15–#19) on entity-surfacing logic — a signal that
  that surface is fragile ([08](08-fragility-map.md)).
