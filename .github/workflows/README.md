# CI Workflows

This directory holds the GitHub Actions workflows that gate merges into
`emissary-v1` (the auto-deploy branch on Render) and `master`.

## ci.yml

Triggered on every PR targeting `emissary-v1` or `master`, plus pushes to
those branches. All five jobs must pass before the PR can merge — enforced
via branch protection rules in GitHub UI (see "Branch protection setup"
below).

| Job | What it checks | Typical runtime | Why it matters |
|---|---|---|---|
| `lint` | `ruff check` + `ruff format --check` | ~10s | Backstop against PRs from machines without pre-commit hooks installed |
| `test` | `pytest tests/ --cov=src` | ~1–2 min | Core gate. Runs all unit + E2E tests in VCR replay mode. No secrets needed. |
| `frontend-build` | `npm ci && npm run build` in `frontend/` | ~2 min | Catches TypeScript errors and Vite build failures before they hit Render |
| `build-script` | `bash build.sh` end-to-end | ~3–5 min | Mirrors Render's deploy environment. Catches submodule / requirements.txt drift |
| `secret-scan` | `gitleaks` against the full diff | ~30s | Prevents `.env` or other secrets from getting committed |

## Debugging a failed run

1. Click "Details" next to the failing job in the PR's Checks tab.
2. Most jobs print actionable output directly:
   - `lint`: shows which files need `ruff format` or have lint errors.
   - `test`: shows the failing test name + traceback.
   - `frontend-build`: shows the TS error or vite build failure.
   - `build-script`: shows which step of `build.sh` exited non-zero.
   - `secret-scan`: shows which file/line gitleaks flagged.
3. Fix locally, push to the same branch — the workflow re-runs automatically.
4. **Do not bypass with `--no-verify` or `[skip ci]`.** If a job is genuinely
   broken (not a real failure), open an issue and tag the workflow file.

## Recording new VCR cassettes

E2E tests for endpoints that call external services (Anthropic, FRED, etc.)
use `vcrpy` cassettes. Recording requires real API keys and burns credits,
so it's a manual local step:

```bash
# 1. Make sure your .env has the required keys
# 2. Record (this hits real services):
VCR_RECORD_MODE=once pytest tests/e2e/test_foo.py

# 3. Redact secrets from the new cassette:
python scripts/redact-cassettes.py

# 4. Verify the cassette replays cleanly:
pytest tests/e2e/test_foo.py

# 5. Commit cassette + test together
git add tests/cassettes/ tests/e2e/test_foo.py
git commit
```

The pre-commit hook will re-run `redact-cassettes.py` to make sure no
secrets slipped through.

## Branch protection setup (one-time, in GitHub UI)

After merging this CI config to `master` or `emissary-v1`:

1. Go to **Settings → Branches → Branch protection rules**.
2. Add a rule for `emissary-v1` (and `master`):
   - ☑ Require a pull request before merging (1 approving review)
   - ☑ Require status checks to pass before merging
   - In the search box, add: `lint`, `test`, `frontend-build`,
     `build-script`, `secret-scan`
   - ☑ Require branches to be up to date before merging
   - ☑ Restrict who can push to matching branches (yourself only)
   - ☑ Do not allow force pushes
3. Save.

After this is in place, a broken PR cannot reach Render's auto-deploy.
