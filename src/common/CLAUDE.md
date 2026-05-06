# src/common/ — Cross-Cutting Concerns

Shared infrastructure that every other package depends on. Touch with care —
a change here ripples through orchestrator, tools, routers, and tests.

## Files

| File | Purpose |
|---|---|
| `types.py` | Pydantic models for inter-layer communication (`ToolResponse`, `Confidence`, `SourceReference`, etc.) |
| `config.py` | Singleton `Config` dataclass loaded from env vars (calls `load_dotenv` at import time, `override=False`) |
| `cache.py` | `diskcache`-backed `get_cached`/`set_cached` for API responses (TTL-based) |
| `http_client.py` | Shared `httpx` client config (timeouts, retries) for all outbound HTTP |
| `rate_limit.py` | slowapi `Limiter` instance + `get_rate_limit_key` (see below) |
| `sanitize.py` | `clamp_for_llm` + `strip_control_chars` for user content destined for Anthropic |

## rate_limit.py — applying limits to a new endpoint

The single source of truth for rate-limit behavior. Three things to know:

### 1. The key function determines who shares a bucket

`get_rate_limit_key(request)` in [rate_limit.py](rate_limit.py) returns:
- `"user:<username>"` when a valid Bearer token is present (authenticated users get isolated buckets)
- `"ip:<address>"` otherwise (anonymous traffic bucketed by source IP)

This means two users behind the same NAT each get their own daily quota,
but one attacker rotating through invalid tokens still hits an IP bucket.

### 2. Storage backend is auto-selected

`_storage_uri()` returns `os.getenv("REDIS_URL")` when set (prod, via Render
env var injection), `"memory://"` otherwise (local dev, CI tests). slowapi
handles the rest — no code changes needed when switching environments.

### 3. To rate-limit a new endpoint, three things must align

```python
# In src/routers/your_router.py:
from fastapi import Request, Response
from src.common.rate_limit import LLM_GENERATE_LIMIT, limiter

@router.post("/your-endpoint")
@limiter.limit(LLM_GENERATE_LIMIT)                                  # 1. decorator
async def your_endpoint(
    request: Request,                                                # 2. Request param FIRST
    response: Response,                                              # 3. Response param SECOND (required by headers_enabled)
    req: YourRequestModel,
):
    ...
```

**Common mistake:** Forgetting `response: Response`. With `headers_enabled=True`
on the limiter (which we have, so X-RateLimit-* headers populate), slowapi
needs to inject a Response to attach headers. Without it you get a confusing
`AttributeError` at request time, not import time. See
[src/routers/coa.py:248-250](../routers/coa.py#L248-L250) for the canonical pattern.

### Limit constants

```python
LLM_GENERATE_LIMIT = "3/minute;30/day"  # for any endpoint that calls Anthropic
ENTITY_RESOLVE_LIMIT = "30/minute"      # for entity resolution / cheap external calls
DEFAULT_LIMITS = ["120/minute"]         # implicit on every endpoint
```

**Convention:** new Anthropic-calling endpoints use `LLM_GENERATE_LIMIT`. Don't
invent a new constant unless the cost profile is genuinely different (see
[CLAUDE.md branch flow](../../CLAUDE.md#branch-flow) and the discussion in PR
#2 if you need historical context).

`30/day` caps a single user at ~$30/day max (Anthropic Sonnet at $0.30–$1.00
per generate call). If your endpoint costs more or less, propose a new
constant in the PR description and update both this doc and `LLM_GENERATE_LIMIT`'s
comment.

## sanitize.py — neutralizing user content before LLM dispatch

Two helpers, applied AFTER Pydantic validation but BEFORE the Anthropic call:

```python
from src.common.sanitize import sanitize_for_llm, strip_control_chars, clamp_for_llm

# Convenience: strip + clamp in one call (this is what routers should use)
user_content = sanitize_for_llm(user_content, max_chars=60_000)

# Or use them separately if you need different limits:
user_content = strip_control_chars(user_content)
user_content = clamp_for_llm(user_content, max_chars=10_000)
```

**Why both:**
- `strip_control_chars` removes ASCII C0 bytes (0x00–0x1F) except tab/newline/CR.
  Defends against prompt-boundary smuggling — an attacker embedding `\x00` to
  confuse tokenizer, or `\x1F` to break terminal logging.
- `clamp_for_llm` enforces a hard ceiling on input size, separately from
  Pydantic's per-field `max_length`. The Pydantic limit is the boundary cap;
  this is the LLM-dispatch cap (typically larger because it applies to the
  whole composed prompt).

**Where to call them:** immediately before `client.messages.create(...)`.
See [src/routers/coa.py](../routers/coa.py) and [src/routers/briefings.py](../routers/briefings.py)
for examples — `user_content = sanitize_for_llm(user_content, max_chars=60_000)`
is the line right before each Anthropic call.

## config.py — adding a new env var

```python
@dataclass
class Config:
    # existing fields ...
    your_new_key: str = field(default_factory=lambda: os.getenv("YOUR_NEW_KEY", ""))

    def validate(self) -> list[str]:
        issues = []
        # ...
        if self.is_required and not self.your_new_key:
            issues.append("YOUR_NEW_KEY is required")
        return issues
```

Then add it to:
1. [.env.example](../../.env.example) as a documented placeholder
2. [render.yaml](../../render.yaml) `envVars` block (use `sync: false` for
   secrets — they get set manually in the Render dashboard, never committed)
3. The `## Critical env vars` table in the root [CLAUDE.md](../../CLAUDE.md)
   if it's required for prod

## What NOT to do

1. **Don't import from `src.routers`, `src.tools`, or `src.orchestrator`** —
   common/ is the BOTTOM of the dependency graph. Imports in the other
   direction would create cycles. Keep this layer source-free except for
   stdlib + third-party deps.
2. **Don't add a singleton without a reset hook for tests.** `config` is OK
   because its values are env-driven and conftest sets envs before import.
   But a singleton DB connection or HTTP client would break test isolation.
3. **Don't change `get_rate_limit_key`'s prefix scheme without updating tests.**
   `tests/e2e/test_rate_limit.py::test_rate_limit_keyed_per_user` asserts that
   different usernames get different buckets. The prefix (`user:` vs `ip:`) is
   what makes the keys debuggable in `redis-cli KEYS LIMITER/*`.
