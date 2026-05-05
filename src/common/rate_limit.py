"""Rate limiter for the Emissary FastAPI app.

Backed by Redis when REDIS_URL is set (production / Render), with an
in-memory fallback for local dev. Wired into src/api.py.

Why Redis: limit counters survive uvicorn restarts and are shared across
workers. The Render emissary service has REDIS_URL injected from the
swarm-redis keyvalue store (see render.yaml fromService block).
"""

from __future__ import annotations

import os

from fastapi import Request
from slowapi import Limiter
from slowapi.util import get_remote_address

from src.auth import verify_token


def _storage_uri() -> str:
    """Return the slowapi storage URI.

    Redis is preferred when REDIS_URL is set; otherwise we fall back to
    in-memory storage (counters reset on process restart).
    """
    redis_url = os.getenv("REDIS_URL", "").strip()
    if redis_url:
        return redis_url
    return "memory://"


def get_rate_limit_key(request: Request) -> str:
    """Compute the bucket key for a given request.

    This is a policy decision — the key determines whose counter gets
    incremented. Two callers with the same key share a single bucket;
    different keys get isolated buckets.

    Trade-offs to consider:
      * Keying purely by IP (slowapi's default `get_remote_address`) is
        simple but punishes everyone behind a corporate NAT when one user
        misbehaves, and gives a single attacker unlimited buckets if they
        rotate IPs.
      * Keying purely by authenticated username gives clean isolation
        for logged-in users but provides no protection on the
        unauthenticated `/api/auth/login` endpoint (an attacker can
        brute-force without consequence).
      * A hybrid — username when an Authorization header is present and
        valid, IP otherwise — gives both: real users get fair treatment,
        anonymous traffic is throttled at the network layer.

    The Authorization header looks like `Bearer <token>`. `verify_token`
    returns the username if the HMAC signature matches, else None.
    `get_remote_address(request)` returns the client IP as a string.

    Recommended return values:
      * "user:<username>" when a valid token is present
      * "ip:<addr>" otherwise
    The prefix makes the key debuggable in `redis-cli KEYS LIMITER/*`.
    """
    auth_header = request.headers.get("Authorization") or request.headers.get("authorization")
    if auth_header and auth_header.startswith("Bearer "):
        token = auth_header[7:]
        username = verify_token(token)
        if username:
            return f"user:{username}"
    return f"ip:{get_remote_address(request)}"


# Default backstop applied to every endpoint unless explicitly overridden.
# Generous enough that healthy demo usage won't trip it; tight enough that
# a runaway client can't spam thousands of requests per minute.
DEFAULT_LIMITS = ["120/minute"]

# Per-endpoint limits applied via @limiter.limit(...) decorators.
# Tuned for demo workloads — see the plan's Phase 1 section for cost math.
LLM_GENERATE_LIMIT = "3/minute;30/day"  # /coa/generate, /briefing/generate
ENTITY_RESOLVE_LIMIT = "30/minute"  # /watchlist/resolve


limiter = Limiter(
    key_func=get_rate_limit_key,
    storage_uri=_storage_uri(),
    default_limits=DEFAULT_LIMITS,
    strategy="moving-window",
    headers_enabled=True,  # adds X-RateLimit-* response headers
)
