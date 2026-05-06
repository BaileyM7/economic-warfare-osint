# .github/ — CI/CD & Repo Metadata

GitHub-specific configuration. The CI workflow ([workflows/ci.yml](workflows/ci.yml))
is the **gate that prevents broken code from reaching `emissary-v1`** (which
auto-deploys to Render). Branch protection in the GitHub UI requires all 5
jobs in this workflow to pass before merging.

For the user-facing "what does each job do" overview, see [workflows/README.md](workflows/README.md).
This CLAUDE.md is for future-Claude maintaining the workflow itself.

## Files

| Path | Purpose |
|---|---|
| `workflows/ci.yml` | The single CI workflow. 5 jobs run in parallel on PR + push to `develop`/`emissary-v1`/`master`. |
| `workflows/README.md` | Human-readable doc + branch protection setup checklist |
| `CODEOWNERS` | Auto-assigns review on security-sensitive paths |
| `pull_request_template.md` | Pre-merge checklist injected into every new PR |

## Adding a new CI job

Three things must align for a new job to actually gate merges:

1. **Add the job to `ci.yml`** — see template below.
2. **Add its `name:` field to GitHub branch protection** — Settings → Branches
   → edit rule → "Require status checks to pass before merging" → search the
   new job's name in the dropdown. **The name only appears after the job has
   run once.** Push a no-op commit to a feature branch to trigger CI, then
   come back and add it.
3. **Update [workflows/README.md](workflows/README.md)** — the table of jobs
   and what each catches.

### Job template

```yaml
  your-new-job:
    name: Your job's display name (Service)
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          submodules: recursive    # see "Submodule gotcha" below
      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: "3.12.7"   # MUST match render.yaml
          cache: pip
      - name: Install deps
        run: |
          pip install --upgrade pip
          pip install -e ".[dev]"
      - name: Your job's actual command
        run: ...
```

### Submodule gotcha

`frontend/` and `swarm/` are git submodules. The default `actions/checkout@v4`
does NOT initialize them. Any job that needs files inside those directories
MUST set `submodules: recursive`:

```yaml
      - uses: actions/checkout@v4
        with:
          submodules: recursive
```

This was a real bug — the original `frontend-build` job failed because
`frontend/package-lock.json` didn't exist in the CI workspace. See PR #2's
fix commit. The same applies to any job that runs anything inside
`frontend/` or `swarm/`.

### Version pinning rule

CI installs Python and Node from the matching versions Render uses ([render.yaml](../render.yaml)
specifies `PYTHON_VERSION=3.12.7` and `NODE_VERSION=20.11.0`). **Don't drift.**
If you bump one of those in render.yaml, bump it here too — otherwise the
`build-script` job will pass on a different version than prod actually uses,
defeating its purpose as a deploy-time-failure detector.

Same rule applies to ruff: pre-commit pins to `v0.15.0` ([../.pre-commit-config.yaml](../.pre-commit-config.yaml))
and CI installs `ruff>=0.4` (which resolves to whatever's latest, currently
`v0.15.x`). If they drift apart, you get a loop where pre-commit reformats
one way and CI rejects the result. Bump both together when updating.

## What CI does NOT cover (and shouldn't pretend to)

| Layer | Where it's actually checked |
|---|---|
| LLM behavior — does Claude give sensible responses? | Manual testing on `develop`. Tests run with `ANTHROPIC_API_KEY=""` so they short-circuit at 503 before calling the API. |
| Frontend interactions — buttons work, forms validate? | Currently nothing. Vitest/Playwright on `frontend/` is unbuilt — `frontend-build` only confirms TS compiles. Future work. |
| Render-specific deploy failures | `build-script` job mimics Render's environment. Catches most. |
| Real network — Anthropic, FRED, etc. responding? | Nothing in CI. By design — VCR replay-only. Live smoke check is manual. |
| Database migrations | N/A — main app uses sqlite with `init_db()` idempotent calls. The wargame subapp uses Alembic but isn't in CI's test scope. |

## Concurrency / cost control

```yaml
concurrency:
  group: ${{ github.workflow }}-${{ github.ref }}
  cancel-in-progress: true
```

Lives at the top of `ci.yml`. Cancels superseded runs on the same branch
when you push a fixup. **Don't remove this.** Without it, every fixup commit
multiplies CI minutes — for a customer-demo POC where you might push 5
fixups in 10 minutes, that's the difference between $0.50 and $5.00 per
merge cycle.

## Secret-scan caveats

The `secret-scan` job runs `gitleaks` against the diff. It catches:
- Anthropic API keys (`sk-ant-...`)
- AWS access key IDs (`AKIA...`)
- Common token patterns

It does NOT catch:
- Custom-shaped secrets (e.g., a vendor-specific token format)
- Secrets in test cassettes that happen to be unique strings (gitleaks doesn't
  know they're sensitive). The `redact-cassettes.py` pre-commit hook is the
  defense for these — see [tests/CLAUDE.md](../tests/CLAUDE.md#recording-vcr-cassettes).

## Branch protection — the rule, not just the workflow

CI alone doesn't gate anything. **Branch protection in the GitHub UI** is what
actually blocks merges. For solo-dev mode, the rules currently configured on
both `develop` and `emissary-v1`:

- ☑ Require status checks: `lint`, `test`, `frontend-build`, `build-script`, `secret-scan`
- ☑ Require branches up to date before merging
- ☑ Disallow force-pushes
- ☐ Require approvals (set to 0 — solo dev)

When adding a collaborator: bump approvals to 1. Don't disable anything else.

## CODEOWNERS conventions

The current `CODEOWNERS` auto-assigns `@BaileyM7` for all changes, with
explicit ownership on security-sensitive paths (`src/auth.py`,
`src/common/rate_limit.py`, `src/common/sanitize.py`, `src/api.py`,
`.github/workflows/`, `.pre-commit-config.yaml`).

When adding a collaborator who specializes in one area, add specific
ownership for that area BEFORE the catch-all `*` line — first-match wins
in CODEOWNERS:

```
src/wargame_backend/  @teammate
src/frontend/         @frontend-specialist
*                     @BaileyM7
```
