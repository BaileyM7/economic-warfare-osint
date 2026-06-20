"""FastAPI web server for the Economic Warfare OSINT system.

Run with:
    uv run uvicorn src.api:app --reload --host 0.0.0.0 --port 8000
"""

from __future__ import annotations

import webbrowser
from typing import Any

from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

import logging

from src.common.analyses import analyses as _analyses
from src.common.config import config
from src.tools.geopolitical.client import refresh_acled_token
from src.db import init_db, seed_mock_data
from src.routers import _shared as _router_shared
from src.routers.coa import router as coa_router
from src.routers.monitoring import router as monitoring_router
from src.routers.briefings import router as briefings_router
from src.routers.risk_feed import router as risk_feed_router
from src.routers.watchlist import router as watchlist_router
from src.routers import briefings as _briefings_mod
from src.analytics import UsageTrackingMiddleware
from src.auth import require_admin, require_auth, verify_token
from src.common.rate_limit import limiter
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from src.routers.admin import router as admin_router
from src.routers.auth import router as auth_router
from src.routers.notifications import router as notifications_router
from src.routers.entity import router as entity_router
from src.routers.sayari import router as sayari_router
from src.routers.screening import router as screening_router
from src.routers.person import router as person_router
from src.routers.sector import router as sector_router
from src.routers.risk import router as risk_router
from src.routers.vessel import router as vessel_router
from src.routers.orchestrator import router as orchestrator_router
from src.routers.sanctions_impact import router as sanctions_impact_router
from src.routers.followup import router as followup_router
from src.routers.briefs import router as briefs_router

logger = logging.getLogger(__name__)


app = FastAPI(
    title="Economic Warfare OSINT",
    description="Multi-agent OSINT system for economic warfare scenario analysis",
    version="0.1.0",
)

app.add_middleware(UsageTrackingMiddleware)

# --- Rate limiting (Redis-backed when REDIS_URL is set) ---
# See src/common/rate_limit.py for key function and limit constants.
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
app.add_middleware(SlowAPIMiddleware)

# --- CORS ---
# Wildcard "*" with allow_credentials=True is unsafe (browsers will reject
# it anyway under modern spec) — read explicit origins from CORS_ORIGINS as
# a comma-separated list. Defaults are dev-only; production sets the env var.
_cors_origins = [o.strip() for o in config.cors_origins.split(",") if o.strip()]
if "*" in _cors_origins:
    raise ValueError(
        "CORS wildcard '*' combined with allow_credentials=True is forbidden. "
        "Set CORS_ORIGINS to explicit origins (comma-separated)."
    )
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)

# --- Include Emissary routers ---
# Auth router is registered FIRST and is NOT gated by auth.
app.include_router(auth_router)
app.include_router(admin_router, dependencies=[Depends(require_admin)])
app.include_router(coa_router, dependencies=[Depends(require_auth)])
app.include_router(monitoring_router, dependencies=[Depends(require_auth)])
app.include_router(briefings_router, dependencies=[Depends(require_auth)])
app.include_router(risk_feed_router, dependencies=[Depends(require_auth)])
# watchlist endpoints all use Depends(require_auth) per-route to read the
# username, so the include-level dep is redundant here — but kept for parity.
app.include_router(watchlist_router, dependencies=[Depends(require_auth)])
# Notifications: cron-token-protected endpoints; no bearer-auth dependency
# (cron services authenticate via the X-Cron-Token shared secret instead).
app.include_router(notifications_router)
app.include_router(entity_router, dependencies=[Depends(require_auth)])
app.include_router(sayari_router, dependencies=[Depends(require_auth)])
app.include_router(screening_router, dependencies=[Depends(require_auth)])
app.include_router(person_router, dependencies=[Depends(require_auth)])
app.include_router(sector_router, dependencies=[Depends(require_auth)])
app.include_router(risk_router, dependencies=[Depends(require_auth)])
app.include_router(vessel_router, dependencies=[Depends(require_auth)])
app.include_router(orchestrator_router, dependencies=[Depends(require_auth)])
app.include_router(sanctions_impact_router, dependencies=[Depends(require_auth)])
app.include_router(followup_router, dependencies=[Depends(require_auth)])
app.include_router(briefs_router, dependencies=[Depends(require_auth)])

# --- Wargame subapp (embedded swarm backend) ---
# Gated by WARGAME_ENABLED so Emissary's baseline behavior is unaffected
# when swarm's Postgres + Redis aren't provisioned. Any import-time failure
# is caught so a broken wargame doesn't take down the rest of the app.

_wargame_app: Any = None  # populated below if import succeeds
_wargame_lifespan_cm: Any = None  # the active lifespan context, kept for shutdown
if config.wargame_enabled:
    try:
        # Swarm's internal code uses bare imports (e.g. `from wargame_backend.X`).
        # Put Emissary's `src/` dir on sys.path so those resolve.
        import sys as _sys

        _src_dir = str(Path(__file__).parent)
        if _src_dir not in _sys.path:
            _sys.path.insert(0, _src_dir)
        from wargame_backend.app.main import app as _wargame_app

        app.mount("/api/wargame", _wargame_app)
        logger.info("Wargame subapp mounted at /api/wargame (%d routes)", len(_wargame_app.routes))
    except Exception as _wargame_exc:  # noqa: BLE001
        _wargame_app = None
        logger.error(
            "Wargame subapp failed to mount; continuing without it. "
            "Set WARGAME_ENABLED=0 to silence this warning. Error: %s",
            _wargame_exc,
        )

# Debug endpoint to read the wargame subapp's lifespan error over HTTP.
# Only active when WARGAME_DEBUG_ERRORS=1.
if config.wargame_debug_errors:

    @app.get("/api/wargame-debug/lifespan")
    async def _wargame_lifespan_debug() -> dict:
        err = None
        try:
            err = getattr(_wargame_app.state, "lifespan_error", None) if _wargame_app else None
        except Exception as e:
            err = f"could not read: {e}"
        has_redis = False
        has_sim_runner = False
        try:
            has_redis = hasattr(_wargame_app.state, "redis") if _wargame_app else False
            has_sim_runner = hasattr(_wargame_app.state, "sim_runner") if _wargame_app else False
        except Exception:
            pass
        return {
            "wargame_app_imported": _wargame_app is not None,
            "lifespan_cm_entered": _wargame_lifespan_cm is not None,
            "has_redis_on_state": has_redis,
            "has_sim_runner_on_state": has_sim_runner,
            "lifespan_error": err,
        }


_DIST = Path(__file__).parent.parent / "frontend" / "dist"
if (_DIST / "assets").exists():
    app.mount("/assets", StaticFiles(directory=str(_DIST / "assets")), name="assets")

_browser_opened = False


@app.on_event("startup")
async def _startup() -> None:
    import os

    global _browser_opened, _wargame_lifespan_cm
    await refresh_acled_token()
    init_db()
    if config.emissary_mock_data:
        seed_mock_data()

    # Surface configuration problems loudly at boot. In production these are
    # usually security/operational issues (default auth secret, missing CORS,
    # notifications enabled without provider creds) that otherwise ship silently.
    for _issue in config.validate():
        _log = logger.error if config.is_production else logger.warning
        _log("CONFIG: %s", _issue)

    # Enter the swarm subapp's lifespan manually. FastAPI does NOT run a
    # mounted sub-app's lifespan automatically — this sets up swarm's DB
    # engine, Redis client, and SimRunner, attaching them to the subapp's
    # state so its endpoints can reach them via request.app.state.*.
    if _wargame_app is not None:
        try:
            _wargame_lifespan_cm = _wargame_app.router.lifespan_context(_wargame_app)
            await _wargame_lifespan_cm.__aenter__()
            logger.info("Wargame lifespan entered (DB + Redis + SimRunner ready)")
        except Exception as exc:  # noqa: BLE001
            _wargame_lifespan_cm = None
            # Log full traceback so Render logs capture the root cause.
            # Also stash the error on the subapp state so /api/wargame/_debug
            # can return it via HTTP when logs are flaky.
            import traceback as _tb

            _err_text = f"{type(exc).__name__}: {exc}\n" + "".join(
                _tb.format_exception(type(exc), exc, exc.__traceback__)
            )
            logger.error("Wargame lifespan failed to enter:\n%s", _err_text)
            # Also write directly to stderr so it bypasses any structlog filters
            import sys as _sys

            print(f"[WARGAME_LIFESPAN_ERROR] {_err_text}", file=_sys.stderr, flush=True)
            try:
                _wargame_app.state.lifespan_error = _err_text
            except Exception:
                pass

    if not _browser_opened and not os.environ.get("RENDER"):
        _browser_opened = True
        try:
            webbrowser.open("http://localhost:8000")
        except Exception:
            pass


@app.on_event("shutdown")
async def _shutdown() -> None:
    """Cleanly exit the swarm subapp's lifespan so DB/Redis connections close."""
    global _wargame_lifespan_cm
    if _wargame_lifespan_cm is not None:
        try:
            await _wargame_lifespan_cm.__aexit__(None, None, None)
            logger.info("Wargame lifespan exited cleanly")
        except Exception as exc:  # noqa: BLE001
            logger.warning("Wargame lifespan exit raised: %s", exc)
        finally:
            _wargame_lifespan_cm = None


# --- WebSocket connection manager for real-time monitoring ---


class ConnectionManager:
    def __init__(self):
        self.connections: list[WebSocket] = []

    async def connect(self, ws: WebSocket):
        await ws.accept()
        self.connections.append(ws)

    def disconnect(self, ws: WebSocket):
        self.connections.remove(ws)

    async def broadcast(self, data: dict):
        dead = []
        for ws in self.connections:
            try:
                await ws.send_json(data)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.connections.remove(ws)


_ws_manager = ConnectionManager()

# Wire the WebSocket manager into the shared router module
_router_shared.init(_ws_manager)


@app.websocket("/ws/monitoring")
async def ws_monitoring(websocket: WebSocket, token: str | None = None):
    # Verify token before accepting
    if not token or not verify_token(token):
        await websocket.close(code=1008)  # Policy violation
        return
    await _ws_manager.connect(websocket)
    try:
        while True:
            await websocket.receive_text()  # keep-alive
    except WebSocketDisconnect:
        _ws_manager.disconnect(websocket)


# --- In-memory state for async orchestrator analyses ---
# The store itself now lives in src.common.analyses (imported above as
# `_analyses`) so the orchestrator router and this module share one object.
# Wire that same dict into the briefing router for its generate endpoint.
_briefings_mod.set_analyses_ref(_analyses)


# --- Request / Response models ---


# --- Health / info ---


@app.get("/")
async def root():
    index = _DIST / "index.html"
    if not index.exists():
        raise HTTPException(
            status_code=503,
            detail="Frontend not built. Run: cd frontend && npm install && npm run build",
        )
    return FileResponse(str(index))


@app.get("/api/health")
async def health():
    issues = config.validate()
    return {
        "status": "ok" if not issues else "misconfigured",
        "issues": issues,
        "model": config.model,
        "tools_available": True,
    }


# --- Static file catch-all (must be LAST route) ---
# Serves root-level files from dist/ (favicon, logos) and SPA fallback


@app.get("/{filename:path}")
async def serve_static_or_spa(filename: str):
    static_file = _DIST / filename
    if static_file.is_file():
        return FileResponse(str(static_file))
    index = _DIST / "index.html"
    if index.exists():
        return FileResponse(str(index))
    raise HTTPException(status_code=404)
