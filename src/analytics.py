"""Usage and login analytics.

Wraps :mod:`src.db.log_usage_event` with higher-level helpers and a FastAPI
middleware that records every authenticated API hit.
"""
from __future__ import annotations

import time
from typing import Callable

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

from src.auth import verify_token
from src.db import log_usage_event


# Map backend path prefixes → frontend tab name. Order matters: more specific first.
_TAB_PREFIXES: list[tuple[str, str]] = [
    ("/api/coa",                "COA Workspace"),
    ("/api/monitoring",         "Monitoring"),
    ("/api/briefing",           "Briefings"),        # matches /api/briefing and /api/briefings
    ("/api/exercise",           "Exercise Control"), # matches /api/exercise and /api/exercises
]

# Endpoints used by the Search page. Anything not in another tab but matching one
# of these goes to "Search". Everything else under /api/ goes to "Other".
_SEARCH_PREFIXES: tuple[str, ...] = (
    "/api/search",
    "/api/analyze",
    "/api/person",
    "/api/vessel-track",
    "/api/entity-graph",
    "/api/resolve-entity",
    "/api/entity-risk-report",
    "/api/sanctions-impact",
    "/api/sanctions/",
    "/api/sayari",
    "/api/sector-analysis",
    "/api/followup",
)

# Paths we never want in the usage stats (noise: polled constantly, internal, or self-referential).
_SKIP_PREFIXES: tuple[str, ...] = (
    "/api/auth/me",     # frontend polls this on every page mount
    "/api/auth/login",  # already captured separately as login_attempt events
    "/api/admin",       # admin viewing the dashboard shouldn't dominate stats
    "/api/health",
    "/assets",
    "/ws",              # websockets
)


def classify_endpoint(path: str, method: str) -> str | None:
    """Bucket a request path into a frontend tab name, or None to skip logging."""
    if method == "OPTIONS":
        return None
    if not path.startswith("/api/"):
        return None
    for skip in _SKIP_PREFIXES:
        if path.startswith(skip):
            return None
    for prefix, tab in _TAB_PREFIXES:
        if path.startswith(prefix):
            return tab
    for prefix in _SEARCH_PREFIXES:
        if path.startswith(prefix):
            return "Search"
    return "Other"


def _client_ip(request: Request) -> str | None:
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else None


def _username_from_request(request: Request) -> str | None:
    auth = request.headers.get("authorization", "")
    if not auth.startswith("Bearer "):
        return None
    return verify_token(auth[7:])


def log_login_attempt(
    username: str,
    success: bool,
    client_ip: str | None = None,
    detail: str | None = None,
) -> None:
    """Record a login attempt. Call from the /api/auth/login handler."""
    log_usage_event(
        kind="login_attempt",
        username=username,
        status_code=200 if success else 401,
        client_ip=client_ip,
        detail=detail,
    )


class UsageTrackingMiddleware(BaseHTTPMiddleware):
    """Log every API request to usage_events, filtered through classify_endpoint."""

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        start = time.perf_counter()
        response: Response = await call_next(request)
        latency_ms = int((time.perf_counter() - start) * 1000)

        path = request.url.path
        method = request.method

        try:
            feature = classify_endpoint(path, method)
        except NotImplementedError:
            # Until the user implements classify_endpoint, log nothing from middleware.
            # Login events are still captured via log_login_attempt in the auth router.
            return response

        if feature is None:
            return response

        log_usage_event(
            kind="api_request",
            username=_username_from_request(request),
            feature=feature,
            path=path,
            method=method,
            status_code=response.status_code,
            latency_ms=latency_ms,
            client_ip=_client_ip(request),
        )
        return response
