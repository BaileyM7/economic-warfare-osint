"""Datalastic AIS client for vessel tracking, position, and port history.

API docs: https://www.datalastic.com/en/api/
Base URL: https://api.datalastic.com/api/v0/

Requires DATALASTIC_API_KEY in environment.  If key is absent the client
returns mock data so the frontend still renders correctly in demo mode.
"""

from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Any

from src.common.cache import get_cached, set_cached
from src.common.config import config
from src.common.http_client import fetch_json

logger = logging.getLogger(__name__)

_BASE = "https://api.datalastic.com/api/v0"
_CACHE_NS = "datalastic"
_CACHE_TTL = 1800  # 30 min — AIS data goes stale fast


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------

async def vessel_find(name: str) -> list[dict[str, Any]]:
    """Search for vessels by name. Returns a list of vessel summaries."""
    api_key = getattr(config, "datalastic_api_key", None)
    if not api_key:
        return _mock_vessel_list(name)

    cached = get_cached(_CACHE_NS, action="find", name=name)
    if cached is not None:
        return cached

    try:
        data = await fetch_json(
            f"{_BASE}/vessel/find",
            params={"api_key": api_key, "name": name},
            timeout=10.0,
        )
        results = data.get("data", []) if isinstance(data, dict) else data
        if isinstance(results, dict):
            results = [results]
        set_cached(_CACHE_NS, results, action="find", name=name, ttl=_CACHE_TTL)
        return results
    except Exception as exc:
        logger.warning("Datalastic vessel_find error: %s", exc)
        return _mock_vessel_list(name)


async def vessel_by_mmsi(mmsi: str) -> dict[str, Any] | None:
    """Get current position and details for a vessel by MMSI."""
    api_key = getattr(config, "datalastic_api_key", None)
    if not api_key:
        return _mock_vessel_detail(mmsi=mmsi)

    cached = get_cached(_CACHE_NS, action="mmsi", mmsi=mmsi)
    if cached is not None:
        return cached

    try:
        data = await fetch_json(
            f"{_BASE}/vessel",
            params={"api_key": api_key, "mmsi": mmsi},
            timeout=10.0,
        )
        result = data.get("data") if isinstance(data, dict) else data
        set_cached(_CACHE_NS, result, action="mmsi", mmsi=mmsi, ttl=_CACHE_TTL)
        return result
    except Exception as exc:
        logger.warning("Datalastic vessel_by_mmsi error: %s", exc)
        return _mock_vessel_detail(mmsi=mmsi)


async def vessel_by_imo(imo: str) -> dict[str, Any] | None:
    """Get current details for a vessel by IMO number."""
    api_key = getattr(config, "datalastic_api_key", None)
    if not api_key:
        return _mock_vessel_detail(imo=imo)

    cached = get_cached(_CACHE_NS, action="imo", imo=imo)
    if cached is not None:
        return cached

    try:
        data = await fetch_json(
            f"{_BASE}/vessel",
            params={"api_key": api_key, "imo": imo},
            timeout=10.0,
        )
        result = data.get("data") if isinstance(data, dict) else data
        set_cached(_CACHE_NS, result, action="imo", imo=imo, ttl=_CACHE_TTL)
        return result
    except Exception as exc:
        logger.warning("Datalastic vessel_by_imo error: %s", exc)
        return _mock_vessel_detail(imo=imo)


async def vessel_history(mmsi: str, days: int = 14) -> list[dict[str, Any]]:
    """Fetch AIS position history for the last *days* days."""
    api_key = getattr(config, "datalastic_api_key", None)
    if not api_key:
        return _mock_vessel_history(mmsi)

    end = date.today()
    start = end - timedelta(days=days)
    cached = get_cached(_CACHE_NS, action="history", mmsi=mmsi, start=str(start))
    if cached is not None:
        return cached

    try:
        data = await fetch_json(
            f"{_BASE}/vessel_history",
            params={
                "api_key": api_key,
                "mmsi": mmsi,
                "startdate": start.isoformat(),
                "enddate": end.isoformat(),
            },
            timeout=15.0,
        )
        results = data.get("data", []) if isinstance(data, dict) else (data or [])
        set_cached(_CACHE_NS, results, action="history", mmsi=mmsi, start=str(start), ttl=_CACHE_TTL)
        return results
    except Exception as exc:
        logger.warning("Datalastic vessel_history error: %s", exc)
        return _mock_vessel_history(mmsi)


# ---------------------------------------------------------------------------
# Mock data (used when DATALASTIC_API_KEY is not set)
# ---------------------------------------------------------------------------

def _mock_vessel_list(name: str) -> list[dict[str, Any]]:
    return [
        {
            "mmsi": "477213600",
            "imo": "9795598",
            "name": name.upper() or "UNKNOWN VESSEL",
            "callsign": "VROA4",
            "flag": "HK",
            "vessel_type": "Bulk Carrier",
            "length": 229,
            "width": 32,
            "deadweight": 81400,
            "latitude": 22.3193,
            "longitude": 114.1694,
            "speed": 0.0,
            "course": 0,
            "status": "Moored",
            "destination": "CNSHA",
            "eta": "2026-03-30T06:00:00Z",
            "last_position_epoch": 1743000000,
            "note": "Demo mode — set DATALASTIC_API_KEY for live data",
        }
    ]


def _mock_vessel_detail(mmsi: str | None = None, imo: str | None = None) -> dict[str, Any]:
    return {
        "mmsi": mmsi or "477213600",
        "imo": imo or "9795598",
        "name": "DEMO VESSEL",
        "callsign": "VROA4",
        "flag": "HK",
        "vessel_type": "Bulk Carrier",
        "length": 229,
        "width": 32,
        "deadweight": 81400,
        "latitude": 31.2304,
        "longitude": 121.4737,
        "speed": 12.4,
        "course": 87,
        "status": "Underway",
        "destination": "SGSIN",
        "eta": "2026-04-02T14:00:00Z",
        "last_position_epoch": 1743100000,
        "note": "Demo mode — set DATALASTIC_API_KEY for live data",
    }


def _mock_vessel_history(mmsi: str) -> list[dict[str, Any]]:
    import math
    base_lat, base_lon = 31.2, 121.4
    points = []
    for i in range(20):
        t = 1742000000 + i * 43200  # 12h steps
        points.append({
            "mmsi": mmsi,
            "latitude": round(base_lat + math.sin(i * 0.3) * 2.5, 4),
            "longitude": round(base_lon + i * 1.8, 4),
            "speed": round(10 + math.sin(i * 0.5) * 4, 1),
            "course": (87 + i * 5) % 360,
            "timestamp": t,
        })
    return points
