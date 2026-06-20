"""API-surface contract tests — the Stage-3 refactor safety net.

These pin the public route surface and the auth gate so that moving handlers
out of the 3,350-line api.py into routers cannot silently drop, rename, or
un-protect an endpoint. When the API intentionally changes (e.g. collapsing the
duplicate /api/follow-up + /api/followup), update EXPECTED_ROUTES in the same PR.
"""

from __future__ import annotations

import pytest

# NOTE: import the app via the `app_module` fixture (not a module-level
# `from src.api import app`). conftest patches env BEFORE that lazy import so the
# route surface is built identically to every other app-using test; a top-level
# import here is collection-time and produced a different app on CI.

# Snapshot of the app's route surface. Excludes: FastAPI built-ins (/docs,
# /redoc, /openapi.json), the conditional /api/wargame* subapp, the conditional
# /assets static mount (only present when frontend/dist is built — not on CI's
# test job), and HEAD methods. 61 routes (58 Phase-2 baseline + Phase-3:
# /api/subscribe, /api/brief/send-now, /api/notifications/diagnostics).
EXPECTED_ROUTES = {
    "DELETE /api/admin/enrollments/{username}",
    "DELETE /api/briefing/{briefing_id}",
    "DELETE /api/coa/{coa_id}",
    "DELETE /api/watchlist/{item_id}",
    "GET /",
    "GET /api/admin/enrollments",
    "GET /api/admin/usage",
    "GET /api/analyze/{analysis_id}",
    "GET /api/auth/me",
    "GET /api/briefing",
    "GET /api/briefing/{briefing_id}",
    "GET /api/coa",
    "GET /api/coa/{coa_id}",
    "GET /api/health",
    "GET /api/monitoring/activity",
    "GET /api/monitoring/kpis",
    "GET /api/monitoring/macro",
    "GET /api/monitoring/map-data",
    "GET /api/risk-feed",
    "GET /api/risk-feed/{item_id}",
    "GET /api/tools",
    "GET /api/watchlist",
    "GET /api/watchlist/suggestions",
    "GET /{filename:path}",
    "PATCH /api/watchlist/{item_id}",
    "POST /api/admin/enrollments",
    "POST /api/admin/enrollments/{username}/test-email",
    "POST /api/admin/enrollments/{username}/test-sms",
    "POST /api/analyze",
    "POST /api/analyze/sync",
    "POST /api/auth/login",
    "POST /api/briefing",
    "POST /api/briefing/generate",
    "POST /api/coa",
    "POST /api/coa/generate",
    "POST /api/entity-graph",
    "POST /api/entity-risk-report",
    "POST /api/followup",
    "POST /api/notifications/send-weekly-digest",
    "POST /api/notifications/twilio/sms-webhook",
    "GET /api/notifications/diagnostics",
    "POST /api/subscribe",
    "POST /api/brief/send-now",
    "POST /api/person-profile",
    "POST /api/person/network",
    "POST /api/person/search",
    "POST /api/resolve-entity",
    "POST /api/risk-feed/refresh",
    "POST /api/risk-feed/{item_id}/prepare-coa",
    "POST /api/sanctions-impact",
    "POST /api/sanctions/screen-batch",
    "POST /api/sayari/related",
    "POST /api/sayari/resolve",
    "POST /api/sayari/ubo",
    "POST /api/sector-analysis",
    "POST /api/vessel-track",
    "POST /api/watchlist",
    "POST /api/watchlist/resolve",
    "PUT /api/briefing/{briefing_id}",
    "PUT /api/coa/{coa_id}",
    "WS_OR_MOUNT /ws/monitoring",
}


def _contract_routes(app) -> set[str]:
    """Flatten the app's route surface, descending into included-router wrappers.

    Newer FastAPI keeps ``include_router()`` routes nested inside a path-less
    internal wrapper (with its own ``.routes``) rather than flattening them into
    ``app.routes``; older FastAPI flattened them inline. Recursing handles both,
    so this snapshot is stable across FastAPI versions (the CI vs. local gap).
    """
    out: set[str] = set()

    def walk(routes) -> None:
        for r in routes:
            path = getattr(r, "path", None)
            if not isinstance(path, str):
                # Path-less wrapper (an included router) — descend into it.
                walk(getattr(r, "routes", []) or [])
                continue
            if path in ("/docs", "/redoc", "/openapi.json", "/docs/oauth2-redirect"):
                continue
            if path.startswith("/assets") or path.startswith("/api/wargame"):
                continue
            methods = sorted(m for m in (getattr(r, "methods", None) or []) if m != "HEAD")
            if methods:
                out.update(f"{m} {path}" for m in methods)
            else:
                out.add(f"WS_OR_MOUNT {path}")

    walk(app.routes)
    return out


def test_route_surface_matches_snapshot(app_module):
    actual = _contract_routes(app_module.app)
    missing = EXPECTED_ROUTES - actual
    added = actual - EXPECTED_ROUTES
    assert not missing and not added, (
        f"\nDropped/renamed routes: {sorted(missing)}"
        f"\nNew/renamed routes:     {sorted(added)}"
        "\nIf this change is intentional, update EXPECTED_ROUTES."
    )


# Endpoints that must reject an unauthenticated request (bearer auth). These are
# the api.py handlers Stage 3 moves into routers; the gate must survive the move.
# (require_auth runs before body validation, so an empty body still yields 401.)
_PROTECTED = [
    ("get", "/api/tools"),
    ("get", "/api/analyze/deadbeef"),
    ("post", "/api/analyze"),
    ("post", "/api/analyze/sync"),
    ("post", "/api/followup"),
    ("post", "/api/sanctions-impact"),
    ("post", "/api/entity-graph"),
    ("post", "/api/resolve-entity"),
    ("post", "/api/person-profile"),
    ("post", "/api/person/search"),
    ("post", "/api/person/network"),
    ("post", "/api/vessel-track"),
    ("post", "/api/sector-analysis"),
    ("post", "/api/entity-risk-report"),
    ("post", "/api/sanctions/screen-batch"),
    ("post", "/api/sayari/resolve"),
    ("post", "/api/sayari/related"),
    ("post", "/api/sayari/ubo"),
    ("post", "/api/subscribe"),
    ("post", "/api/brief/send-now"),
]


@pytest.mark.parametrize("method,path", _PROTECTED)
def test_protected_endpoint_rejects_unauthenticated(app_client, method, path):
    client_method = getattr(app_client, method)
    resp = client_method(path) if method == "get" else client_method(path, json={})
    assert resp.status_code == 401, (
        f"{method.upper()} {path} returned {resp.status_code}, expected 401"
    )
