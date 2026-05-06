# src/routers/ — FastAPI Routers

HTTP entry points. Every file here exposes a router included in [src/api.py](../api.py)
under `/api/<scope>`. The wargame subapp has its own routers under
[src/wargame_backend/](../wargame_backend/) with separate conventions.

## File-to-prefix map

| File | Prefix | Auth | Notes |
|---|---|---|---|
| `auth.py` | `/api/auth` | None on `/login` | Returns Bearer tokens |
| `admin.py` | `/api/admin` | `require_admin` | Usage analytics |
| `coa.py` | `/api/coa` | `require_auth` | Course-of-action CRUD + LLM generation |
| `briefings.py` | `/api/briefing` | `require_auth` | Briefing CRUD + LLM generation |
| `monitoring.py` | `/api/monitoring` | `require_auth` | KPIs, activity feed, geo data |
| `risk_feed.py` | `/api/risk-feed` | `require_auth` | Per-user risk feed |
| `watchlist.py` | `/api/watchlist` | `require_auth` | Per-user watch-list CRUD + entity resolution |
| `_shared.py` | n/a | n/a | Shared helpers (`notify_monitoring`, etc.) |

## The standard endpoint pattern

**Every new endpoint must follow this template** to inherit the project's
security/cost/auth posture. Skipping any layer creates a real incident class —
prompt injection, credit drain, SQL injection, or auth bypass.

```python
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field, field_validator

from src.auth import require_auth
from src.common.rate_limit import LLM_GENERATE_LIMIT, limiter
from src.common.sanitize import sanitize_for_llm
from src.db import get_db
from src.llm import get_anthropic_client

# 1. Pydantic model with HARD constraints — no bare `str`/`dict` fields
class GenerateRequest(BaseModel):
    objective: str = Field(..., min_length=1, max_length=1_000)
    analysis_data: dict | None = None

    @field_validator("analysis_data")
    @classmethod
    def _cap_size(cls, v):
        # Cap serialized size to limit per-request cost
        if v is not None and len(json.dumps(v, default=str)) > 50_000:
            raise ValueError("analysis_data exceeds 50KB serialized size")
        return v

# 2. Endpoint signature — Request + Response BEFORE the body model
@router.post("/your-endpoint")
@limiter.limit(LLM_GENERATE_LIMIT)                    # rate-limit decorator
async def your_endpoint(
    request: Request,                                  # required by slowapi
    response: Response,                                # required when headers_enabled=True
    req: GenerateRequest,                              # validated body
    username: str = Depends(require_auth),             # auth gate
):
    # 3. Compose user content
    user_content = f"OBJECTIVE: {req.objective}\n\nDATA:\n{json.dumps(req.analysis_data)}"

    # 4. Sanitize before LLM dispatch — this is the line you must not forget
    user_content = sanitize_for_llm(user_content, max_chars=60_000)

    # 5. Now call Anthropic
    client = get_anthropic_client()
    if not client:
        raise HTTPException(503, "Anthropic API key not configured")
    response_msg = await client.messages.create(
        model=config.model,
        max_tokens=3000,
        system=YOUR_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_content}],
    )
    return {...}
```

## What each layer protects against

| Layer | Without it, attacker can... |
|---|---|
| Pydantic `Field(..., max_length=...)` | Send 100MB strings → bandwidth + token costs |
| Pydantic `pattern=r"..."` on IDs | Inject SQL fragments via path/body params |
| Custom `field_validator` for size budget | Compose small JSON containing massive nested values |
| `@limiter.limit(LLM_GENERATE_LIMIT)` | Hit `/coa/generate` 1000×/min → ~$1000 in 60 seconds |
| `Depends(require_auth)` | Hit any authenticated endpoint with no token |
| `sanitize_for_llm(...)` | Embed control bytes (`\x00`, `\x1F`) to confuse tokenizer |
| Sanitize BEFORE Anthropic call | Smuggle prompt-boundary breaks via newlines |

## Pydantic conventions in this repo

- **Always use `Field(...)` with explicit constraints**, not bare types. A
  bare `str` field is an incident waiting to happen.
- **String fields get `max_length` minimum**, plus `pattern` if the field is
  a known shape (UUIDs, slugs, ticker symbols).
- **ID-shaped fields** (`coa_id`, `analysis_id`, etc.) use the shared
  `_ID_PATTERN = r"^[a-zA-Z0-9_-]+$"` (defined in [briefings.py](briefings.py))
  with `max_length=64`. This catches SQL injection attempts at the boundary.
- **Numeric fields with semantic ranges** use `ge=` and `le=`. Confidence is
  always `Field(None, ge=0.0, le=1.0)` — it's a probability.
- **List fields** get `max_length=N` to cap fan-out (e.g., `target_entities:
  list[str] = Field(default_factory=list, max_length=50)`).
- **Dict fields** get a `field_validator` that caps serialized size.
  See [coa.py:55-68](coa.py#L55-L68) for `COAGenerateRequest._cap_analysis_size`
  capping `analysis_data` at 50KB.

## Rate-limit policy for new endpoints

| Endpoint type | Use this constant | Why |
|---|---|---|
| Calls Anthropic (chat / generate) | `LLM_GENERATE_LIMIT` (`3/minute;30/day`) | $30/day max per user @ ~$1/call |
| Cheap external lookup (yfinance, OFAC) | `ENTITY_RESOLVE_LIMIT` (`30/minute`) | Cost is tiny but rate limits matter |
| Pure DB CRUD | (none — relies on `DEFAULT_LIMITS` of `120/min`) | DB writes are cheap; default backstop is enough |

**If your endpoint doesn't fit any of these**, propose a new constant in
[src/common/rate_limit.py](../common/rate_limit.py) and update [src/common/CLAUDE.md](../common/CLAUDE.md)
to explain when to use it.

## SQL: parameterized queries only

Every router uses raw SQL via `sqlite3.Connection.execute(...)` with `?`
placeholders. **String concatenation into SQL is forbidden.** Test
[tests/e2e/test_input_validation.py::test_sql_injection_in_entity_name_is_stored_as_data](../../tests/e2e/test_input_validation.py)
proves the codebase round-trips `'; DROP TABLE coas; --` as literal data.

When updating multiple columns, use a dict + `set_clause` pattern:

```python
updates = {}
for field in ("status", "title"):  # whitelist of column names — NEVER from user input
    val = getattr(req, field, None)
    if val is not None:
        updates[field] = val
set_clause = ", ".join(f"{k} = ?" for k in updates)
conn.execute(f"UPDATE foo SET {set_clause} WHERE id = ?", (*updates.values(), foo_id))
```

The keys in `updates` come from a hardcoded tuple, never from `req.dict()`.
This is the only safe way to dynamically build SET clauses.

## Adding a new endpoint — checklist

When you open a PR adding a new endpoint, your reviewer should be able to tick
each of these:

- [ ] Pydantic request model has `Field(...)` with `max_length` on every string
- [ ] If it accepts an ID, `pattern` enforces a safe shape
- [ ] If it calls Anthropic: `@limiter.limit(LLM_GENERATE_LIMIT)` and `request: Request, response: Response` in signature
- [ ] If it accepts user text into a prompt: `sanitize_for_llm(user_content, max_chars=...)` immediately before the Anthropic call
- [ ] `Depends(require_auth)` (or `require_admin` for admin-only) gates access
- [ ] Any new SQL uses `?` placeholders, no f-string interpolation of values
- [ ] An E2E test in `tests/e2e/` covers the happy path AND a validation rejection
- [ ] If the endpoint costs money per call, the rate-limit constant matches the cost profile

## Rate limit gotchas (you will hit these)

1. **Order matters:** `@router.post(...)` → `@limiter.limit(...)` → `async def`.
   The decorator nearest the function is `limiter.limit`. Reverse order silently
   no-ops the limiter.
2. **`request: Request` MUST be the first param after decorators**, otherwise
   slowapi can't find it to call the key function. `response: Response`
   must follow it (required by `headers_enabled=True`).
3. **Tests reset the bucket per `app_client` fixture**, but slowapi's
   moving-window can leak across tests if you reuse the client. The conftest
   creates a fresh client per test to avoid this.
4. **The 30/day quota uses real wall-clock time.** A test that hits the
   endpoint 30 times legitimately will exhaust the daily quota for that user
   key. Use `src.auth.create_token("uniqueforthistest")` to get isolated
   buckets per test. See [tests/e2e/test_rate_limit.py::test_rate_limit_keyed_per_user](../../tests/e2e/test_rate_limit.py).
