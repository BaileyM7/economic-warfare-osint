"""Shared WebSocket manager and notification helper for Emissary routers.

Avoids circular imports: routers import from here instead of from src.api.
The api module calls ``init(ws_manager)`` at startup to wire the real manager.
"""

from __future__ import annotations

import json
from typing import Any

from src.db import get_db


# ---- WebSocket manager reference (set by api.py at startup) ----

_ws_manager: Any = None


def init(ws_manager: Any) -> None:
    """Called once from api.py to inject the live ConnectionManager."""
    global _ws_manager
    _ws_manager = ws_manager


async def notify_monitoring(event_type: str, message: str) -> None:
    """Broadcast a monitoring update to all WebSocket clients."""
    if _ws_manager is None:
        return
    try:
        conn = get_db()
        try:
            active_coas = conn.execute("SELECT COUNT(*) FROM coas WHERE status IN ('approved','executing')").fetchone()[0]
            total_coas = conn.execute("SELECT COUNT(*) FROM coas").fetchone()[0]
            total_activity = conn.execute("SELECT COUNT(*) FROM activity_log").fetchone()[0]
            total_briefings = conn.execute("SELECT COUNT(*) FROM briefings").fetchone()[0]
            last_row = conn.execute("SELECT * FROM activity_log ORDER BY id DESC LIMIT 1").fetchone()
        finally:
            conn.close()

        await _ws_manager.broadcast({
            "type": "kpi_update",
            "kpis": {
                "active_coas": active_coas,
                "total_coas": total_coas,
                "total_activity": total_activity,
                "last_event": last_row["timestamp"] if last_row else None,
                "total_briefings": total_briefings,
            },
            "latest_activity": dict(last_row) if last_row else None,
        })
    except Exception:
        pass  # non-critical
