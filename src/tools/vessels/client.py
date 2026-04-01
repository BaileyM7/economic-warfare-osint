"""Datalastic AIS client for vessel tracking, position, and port history.

API docs: https://www.datalastic.com/en/api/
Base URL: https://api.datalastic.com/api/v0/

Requires DATALASTIC_API_KEY in environment.  Falls back to OpenSanctions
vessel schema search when no key is configured (no position/history data).
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
_CACHE_TTL = 1800  # 30 min


def _api_key() -> str | None:
    key = getattr(config, "datalastic_api_key", None)
    return key if key else None


# ---------------------------------------------------------------------------
# Datalastic API (real AIS data)
# ---------------------------------------------------------------------------

async def vessel_find(name: str) -> list[dict[str, Any]]:
    """Search for vessels by name. Returns list of vessel summaries."""
    key = _api_key()
    if not key:
        logger.warning("No DATALASTIC_API_KEY — vessel search unavailable")
        return []

    cached = get_cached(_CACHE_NS, action="find", name=name)
    if cached is not None:
        return cached

    try:
        data = await fetch_json(
            f"{_BASE}/vessel_find",
            params={"api-key": key, "name": name},
            timeout=10.0,
        )
        results = data.get("data", [])
        if isinstance(results, dict):
            results = [results]
        # Normalize field names
        normalized = [_normalize_vessel(r) for r in results]
        set_cached(normalized, _CACHE_NS, action="find", name=name, ttl=_CACHE_TTL)
        return normalized
    except Exception as exc:
        logger.warning("Datalastic vessel_find error: %s", exc)
        return []


async def vessel_by_mmsi(mmsi: str) -> dict[str, Any] | None:
    """Get current position and details for a vessel by MMSI."""
    key = _api_key()
    if not key:
        return None

    cached = get_cached(_CACHE_NS, action="mmsi", mmsi=mmsi)
    if cached is not None:
        return cached

    try:
        data = await fetch_json(
            f"{_BASE}/vessel",
            params={"api-key": key, "mmsi": mmsi},
            timeout=10.0,
        )
        result = _normalize_vessel(data.get("data", {}))
        set_cached(result, _CACHE_NS, action="mmsi", mmsi=mmsi, ttl=_CACHE_TTL)
        return result
    except Exception as exc:
        logger.warning("Datalastic vessel_by_mmsi error: %s", exc)
        return None


async def vessel_by_imo(imo: str) -> dict[str, Any] | None:
    """Get current details for a vessel by IMO number."""
    key = _api_key()
    if not key:
        return None

    cached = get_cached(_CACHE_NS, action="imo", imo=imo)
    if cached is not None:
        return cached

    try:
        data = await fetch_json(
            f"{_BASE}/vessel",
            params={"api-key": key, "imo": imo},
            timeout=10.0,
        )
        result = _normalize_vessel(data.get("data", {}))
        set_cached(result, _CACHE_NS, action="imo", imo=imo, ttl=_CACHE_TTL)
        return result
    except Exception as exc:
        logger.warning("Datalastic vessel_by_imo error: %s", exc)
        return None


async def vessel_history(mmsi: str, days: int = 30) -> list[dict[str, Any]]:
    """Fetch AIS position history for the last *days* days.

    Returns list of position dicts with: latitude, longitude, speed, course, timestamp.
    Supports up to 30 days for the 24h/1w/1m map toggles.
    """
    key = _api_key()
    if not key:
        logger.warning("No DATALASTIC_API_KEY — vessel history unavailable")
        return []

    end_dt = date.today()
    start_dt = end_dt - timedelta(days=days)

    cached = get_cached(_CACHE_NS, action="history", mmsi=mmsi, days=days)
    if cached is not None:
        return cached

    try:
        data = await fetch_json(
            f"{_BASE}/vessel_history",
            params={
                "api-key": key,
                "mmsi": mmsi,
                "date_from": start_dt.isoformat(),
                "date_to": end_dt.isoformat(),
            },
            timeout=20.0,
        )
        # Response: data.positions[] with lat, lon, speed, course, last_position_epoch
        raw_positions = data.get("data", {}).get("positions", [])
        positions = [_normalize_position(p) for p in raw_positions]
        set_cached(positions, _CACHE_NS, action="history", mmsi=mmsi, days=days, ttl=_CACHE_TTL)
        return positions
    except Exception as exc:
        logger.warning("Datalastic vessel_history error: %s", exc)
        return []


# ---------------------------------------------------------------------------
# Normalization — map Datalastic fields to our standard shape
# ---------------------------------------------------------------------------

def _normalize_vessel(raw: dict[str, Any]) -> dict[str, Any]:
    """Normalize Datalastic vessel response to a consistent shape."""
    return {
        "name": raw.get("name") or raw.get("name_ais") or "Unknown",
        "imo": raw.get("imo") or "",
        "mmsi": raw.get("mmsi") or "",
        "callsign": raw.get("callsign") or "",
        "flag": raw.get("country_iso") or "",
        "vessel_type": raw.get("type_specific") or raw.get("type") or "",
        "length": raw.get("length") or raw.get("a") or None,
        "width": raw.get("width") or raw.get("b") or None,
        "deadweight": raw.get("dwt") or raw.get("deadweight") or 0,
        "latitude": raw.get("lat") or 0.0,
        "longitude": raw.get("lon") or 0.0,
        "speed": raw.get("speed") or 0.0,
        "course": raw.get("course") or 0,
        "heading": raw.get("heading") or 0,
        "status": raw.get("navigation_status") or "Unknown",
        "destination": raw.get("destination") or "",
        "eta": raw.get("eta_UTC") or raw.get("eta") or "",
        "last_position_epoch": raw.get("last_position_epoch") or 0,
        "source": "Datalastic AIS",
    }


def _normalize_position(raw: dict[str, Any]) -> dict[str, Any]:
    """Normalize a single AIS position point."""
    return {
        "latitude": raw.get("lat") or 0.0,
        "longitude": raw.get("lon") or 0.0,
        "speed": raw.get("speed") or 0.0,
        "course": raw.get("course") or 0,
        "timestamp": raw.get("last_position_epoch") or 0,
    }


# ---------------------------------------------------------------------------
# Port call history (Datalastic API)
# ---------------------------------------------------------------------------

async def vessel_port_calls(mmsi: str, days: int = 90) -> list[dict[str, Any]]:
    """Fetch port call history from Datalastic.

    Returns list of port calls with: port_name, country, arrival, departure, duration.
    Falls back to infer_port_stops() if no Datalastic key or API fails.
    """
    key = _api_key()
    if not key:
        return []

    cached = get_cached(_CACHE_NS, action="port_calls", mmsi=mmsi, days=days)
    if cached is not None:
        return cached

    end_dt = date.today()
    start_dt = end_dt - timedelta(days=days)

    try:
        data = await fetch_json(
            f"{_BASE}/port_calls",
            params={
                "api-key": key,
                "mmsi": mmsi,
                "date_from": start_dt.isoformat(),
                "date_to": end_dt.isoformat(),
            },
            timeout=15.0,
        )
        raw_calls = data.get("data", [])
        if isinstance(raw_calls, dict):
            raw_calls = [raw_calls]
        calls = [_normalize_port_call(c) for c in raw_calls]
        set_cached(calls, _CACHE_NS, action="port_calls", mmsi=mmsi, days=days, ttl=_CACHE_TTL)
        return calls
    except Exception as exc:
        logger.warning("Datalastic port_calls error: %s", exc)
        return []


def _normalize_port_call(raw: dict[str, Any]) -> dict[str, Any]:
    """Normalize a Datalastic port call record."""
    return {
        "port_name": raw.get("port_name") or raw.get("port", {}).get("name", "Unknown"),
        "country": raw.get("country_iso") or raw.get("port", {}).get("country_iso", ""),
        "latitude": raw.get("lat") or 0.0,
        "longitude": raw.get("lon") or 0.0,
        "arrival": raw.get("arrival") or raw.get("timestamp_arrival") or "",
        "departure": raw.get("departure") or raw.get("timestamp_departure") or "",
    }


# ---------------------------------------------------------------------------
# Port stop inference from AIS positions (fallback)
# ---------------------------------------------------------------------------

def infer_port_stops(positions: list[dict[str, Any]], speed_threshold: float = 1.0) -> list[dict[str, Any]]:
    """Infer port stops from AIS position history by detecting low-speed clusters.

    Groups consecutive positions where speed < threshold into stops,
    returns unique stops deduplicated by proximity (within ~10km).
    """
    if not positions:
        return []

    stops: list[dict[str, Any]] = []
    current_stop: list[dict[str, Any]] = []

    for pos in sorted(positions, key=lambda p: p.get("timestamp", 0)):
        speed = pos.get("speed", 99)
        if speed <= speed_threshold:
            current_stop.append(pos)
        else:
            if len(current_stop) >= 2:
                stops.append(_cluster_to_stop(current_stop))
            current_stop = []

    if len(current_stop) >= 2:
        stops.append(_cluster_to_stop(current_stop))

    # Deduplicate by proximity (~0.1 degree ≈ 11km)
    unique: list[dict[str, Any]] = []
    for stop in stops:
        is_dup = False
        for u in unique:
            if abs(stop["latitude"] - u["latitude"]) < 0.1 and abs(stop["longitude"] - u["longitude"]) < 0.1:
                is_dup = True
                break
        if not is_dup:
            unique.append(stop)

    return unique


def _cluster_to_stop(positions: list[dict[str, Any]]) -> dict[str, Any]:
    """Convert a cluster of low-speed positions to a stop record."""
    avg_lat = sum(p["latitude"] for p in positions) / len(positions)
    avg_lon = sum(p["longitude"] for p in positions) / len(positions)
    first_ts = min(p.get("timestamp", 0) for p in positions)
    last_ts = max(p.get("timestamp", 0) for p in positions)
    return {
        "latitude": round(avg_lat, 4),
        "longitude": round(avg_lon, 4),
        "arrival_ts": first_ts,
        "departure_ts": last_ts,
        "duration_hours": round((last_ts - first_ts) / 3600, 1) if last_ts > first_ts else 0,
        "position_count": len(positions),
    }
