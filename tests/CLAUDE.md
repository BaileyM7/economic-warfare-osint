# tests/ — Test Suite

Pytest with FastAPI's `TestClient`, plus VCR.py for record/replay of external
HTTP. Replay is the default — tests should never make real network calls
unless explicitly recording. The full suite (60 tests) runs in ~16 seconds
with **zero Anthropic spend**.

## Layout

```
tests/
  conftest.py                       # session-wide fixtures (app_client, auth_headers, vcr_config)
  test_person_search.py             # legacy unit tests, no app boot
  test_tools_import.py              # import-only sanity tests
  e2e/
    __init__.py
    test_input_validation.py        # Pydantic boundary rejections
    test_rate_limit.py              # slowapi 429 behavior
    test_watchlist_e2e.py           # CRUD + max-active cap
  cassettes/<test_module>/<test_name>/recording.yaml   # VCR cassettes (when recorded)
```

## How tests are isolated from the real world

[conftest.py](conftest.py) does three things BEFORE any `src.*` import:

1. **Clears `REDIS_URL`** — slowapi falls back to in-memory storage, no Redis
   needed in CI.
2. **Sets `ANTHROPIC_API_KEY=""`** — endpoints that call the LLM fast-fail at
   503 instead of making real API calls. This is why the test suite costs $0
   to run, and why it runs in seconds instead of minutes.
3. **Patches `src.db.DB_PATH`** to a temp file so each test gets a clean
   SQLite. The DB is wiped between tests (the `app_client` fixture re-runs
   `init_db()`).

`load_dotenv()` is `override=False` by default, so the env vars set in conftest
win even when `.env` is present.

## Adding a new E2E test

```python
def test_something(app_client, auth_headers):
    resp = app_client.post(
        "/api/your-endpoint",
        json={...},
        headers=auth_headers,    # <-- bearer token from demo creds
    )
    assert resp.status_code == 200
```

Available fixtures (defined in [conftest.py](conftest.py)):
- `app_client` — `TestClient` against the real FastAPI app, fresh DB per test
- `auth_token` — Bearer token string from logging in as `tester:testpass`
- `auth_headers` — pre-built `{"Authorization": "Bearer ..."}` dict
- `app_module` — the `src.api` module itself (use this to access internals like `app_module.app.state.limiter`)

## Recording VCR cassettes (for endpoints that must hit real Anthropic)

Endpoints like `/coa/generate` cost real money to call live. Record once,
replay forever. Workflow:

```bash
# 1. Make sure your local .env has the keys the endpoint needs (ANTHROPIC_API_KEY etc.)
# 2. Record (real API call, ~$0.05–$0.30):
VCR_RECORD_MODE=once pytest tests/e2e/test_foo.py::test_generate_with_cassette

# 3. Scrub secrets from the new cassette:
python scripts/redact-cassettes.py
# (replaces all .env values >=12 chars with the literal string "<redacted>")

# 4. Verify replay works (no network):
pytest tests/e2e/test_foo.py::test_generate_with_cassette

# 5. Commit cassette + test together
git add tests/cassettes/test_foo/ tests/e2e/test_foo.py
```

The pre-commit hook re-runs `redact-cassettes.py` if any cassette is staged —
defense against forgetting step 3. But the hook is value-blind: secrets that
came from env vars NOT in `.env` (shell exports, `~/.aws/credentials`) leak
silently. Audit the cassette diff before committing if you're not sure.

## Test categories — what each gates

| File | Catches |
|---|---|
| `test_input_validation.py` | Regression in Pydantic constraints. SQL injection isn't blocked by Pydantic but stays as data (round-trip test). |
| `test_rate_limit.py` | Decorator regressions, key-function changes, header injection. Verifies per-user bucketing isolation. |
| `test_watchlist_e2e.py` | CRUD + the `MAX_ACTIVE_PER_USER=10` cap. |
| `test_tools_import.py` | Module-level import errors (the bug PR #2 fixed). |

## Conventions

- **Use `assert ... == ...`, not unittest-style `self.assertEqual`.** Pytest's
  rewriting gives better failure messages.
- **One concept per test.** Splitting `test_thing_happy_path` from
  `test_thing_rejects_invalid_x` makes failures readable.
- **Don't mock if a fixture exists.** `app_client` is faster to use than mocking
  the entire FastAPI app.
- **For tests that need a different auth user, use `src.auth.create_token("name")`
  directly** — it produces valid HMAC tokens against the test secret.

## When tests pass locally but fail in CI

Most likely causes, in order:
1. Submodule not checked out in CI (`actions/checkout` needs `submodules: recursive`).
2. Different ruff version → format check fails. Pre-commit pin and CI install
   should match (both at `>=0.4` resolving to same minor).
3. `ANTHROPIC_API_KEY` set in CI when it shouldn't be — would change which
   branch of conditional code executes. Conftest forces it to empty; check
   the test's env doesn't override.
4. Timing-sensitive tests (rate limiter at 60s window) — flaky if CI is slow.
   Use `moving-window` strategy + key on per-test bucket if you see this.

## Running specific subsets

```bash
pytest tests/e2e/                    # E2E only
pytest tests/ -k "rate_limit"        # name match
pytest tests/ --cov=src              # with coverage
pytest tests/ -x                     # stop on first failure (what pre-push uses)
pytest tests/ --tb=short             # shorter tracebacks
```
