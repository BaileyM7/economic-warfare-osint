"""Datalastic AIS client for vessel tracking, position, and port history.

API docs: https://www.datalastic.com/en/api/
Base URL: https://api.datalastic.com/api/v0/

Requires DATALASTIC_API_KEY in environment.  If key is absent, the client
falls back to OpenSanctions vessel schema search, then to a curated fixture
file at data/fixtures/vessels.json.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from src.common.cache import get_cached, set_cached
from src.common.config import config
from src.common.http_client import fetch_json

logger = logging.getLogger(__name__)

_BASE = "https://api.datalastic.com/api/v0"
_OPENSANCTIONS_BASE = "https://api.opensanctions.org"
_CACHE_NS = "datalastic"
_OPENSANCTIONS_CACHE_NS = "opensanctions_vessel"
_CACHE_TTL = 1800  # 30 min — AIS data goes stale fast
_OS_CACHE_TTL = 3600  # 1h for OpenSanctions results

# Path to fixture fallback
_FIXTURES_PATH = Path(__file__).parent.parent.parent.parent / "data" / "fixtures" / "vessels.json"


# ---------------------------------------------------------------------------
# OpenSanctions vessel search (free, no API key required)
# ---------------------------------------------------------------------------

def _parse_opensanctions_vessel(entity: dict[str, Any]) -> dict[str, Any]:
    """Convert an OpenSanctions entity dict to Datalastic-compatible shape."""
    props = entity.get("properties", {})

    def _first(key: str) -> str | None:
        vals = props.get(key, [])
        return vals[0] if vals else None

    imo = _first("imoNumber")
    mmsi = _first("mmsi")
    flag = _first("flag")
    name = entity.get("caption") or _first("name") or "Unknown"
    vessel_type = _first("type") or _first("buildMaterial") or "Unknown"
    owner = _first("owner") or _first("operator")

    # Sanction programs from the entity's datasets/topics
    sanction_programs = []
    for dataset in entity.get("datasets", []):
        if dataset not in ("default", "sanctions"):
            sanction_programs.append(dataset.upper())
    topics = entity.get("topics", [])

    return {
        "mmsi": mmsi or "",
        "imo": imo or "",
        "name": name.upper(),
        "flag": flag or "",
        "vessel_type": vessel_type,
        "deadweight": 0,
        "latitude": 0.0,
        "longitude": 0.0,
        "speed": 0.0,
        "status": "Unknown",
        "destination": "",
        "last_position_epoch": 0,
        "owner": owner or "",
        "sanction_programs": sanction_programs,
        "topics": topics,
        "opensanctions_id": entity.get("id", ""),
        "source": "OpenSanctions",
    }


def _extract_os_results(data: Any) -> list[dict[str, Any]]:
    """Extract OpenSanctions entity rows from common response shapes."""
    if isinstance(data, dict):
        if isinstance(data.get("results"), list):
            return data["results"]
        if isinstance(data.get("data"), list):
            return data["data"]
    return []


async def _opensanctions_vessel_search(query: str, limit: int = 10) -> list[dict[str, Any]]:
    """Search OpenSanctions vessels using API key when present, public endpoint otherwise."""
    api_key = getattr(config, "opensanctions_api_key", None)

    if api_key:
        data = await fetch_json(
            f"{_OPENSANCTIONS_BASE}/search/default",
            params={"q": query, "schema": "Vessel", "limit": limit},
            headers={"Authorization": f"ApiKey {api_key}"},
            timeout=15.0,
        )
        return _extract_os_results(data)

    data = await fetch_json(
        f"{_OPENSANCTIONS_BASE}/entities/_search",
        params={"q": query, "schema": "Vessel", "limit": limit},
        timeout=15.0,
    )
    return _extract_os_results(data)


async def vessel_find_opensanctions(name: str) -> list[dict[str, Any]]:
    """Search OpenSanctions for vessels by name (schema=Vessel).

    Returns a list of vessel dicts in Datalastic-compatible shape.
    """
    cached = get_cached(_OPENSANCTIONS_CACHE_NS, action="find", name=name)
    if cached is not None:
        return cached

    try:
        results_raw = await _opensanctions_vessel_search(name, limit=10)
        results = [_parse_opensanctions_vessel(e) for e in results_raw]
        set_cached(_OPENSANCTIONS_CACHE_NS, results, action="find", name=name, ttl=_OS_CACHE_TTL)
        return results
    except Exception as exc:
        logger.warning("OpenSanctions vessel search error: %s", exc)
        return []


async def vessel_find_by_imo_opensanctions(imo: str) -> dict[str, Any] | None:
    """Look up a vessel in OpenSanctions by IMO number."""
    cached = get_cached(_OPENSANCTIONS_CACHE_NS, action="imo", imo=imo)
    if cached is not None:
        return cached

    try:
        results_raw = await _opensanctions_vessel_search(imo, limit=5)
        for entity in results_raw:
            props = entity.get("properties", {})
            imo_vals = props.get("imoNumber", [])
            if imo in imo_vals:
                result = _parse_opensanctions_vessel(entity)
                set_cached(_OPENSANCTIONS_CACHE_NS, result, action="imo", imo=imo, ttl=_OS_CACHE_TTL)
                return result
    except Exception as exc:
        logger.warning("OpenSanctions vessel IMO lookup error: %s", exc)
    return None


# ---------------------------------------------------------------------------
# Fixture fallback
# ---------------------------------------------------------------------------

def _load_fixtures() -> list[dict[str, Any]]:
    """Load curated vessel fixtures from data/fixtures/vessels.json."""
    try:
        if _FIXTURES_PATH.exists():
            with open(_FIXTURES_PATH, encoding="utf-8") as f:
                return json.load(f)
    except Exception as exc:
        logger.warning("Failed to load vessel fixtures: %s", exc)
    return []


def _find_in_fixtures(query: str, fixtures: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Search fixtures by name, IMO, or MMSI."""
    q = query.lower().strip()
    return [
        v for v in fixtures
        if q in v.get("name", "").lower()
        or q == v.get("imo", "").lower()
        or q == v.get("mmsi", "").lower()
    ]


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------

async def vessel_find(name: str) -> list[dict[str, Any]]:
    """Search for vessels by name.

    Priority: Datalastic (if key set) → OpenSanctions → fixture fallback.
    """
    api_key = getattr(config, "datalastic_api_key", None)
    if api_key:
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

    # OpenSanctions primary fallback
    os_results = await vessel_find_opensanctions(name)
    if os_results:
        return os_results

    # Fixture secondary fallback
    fixtures = _load_fixtures()
    fixture_hits = _find_in_fixtures(name, fixtures)
    if fixture_hits:
        return fixture_hits

    # Nothing found
    return []


async def vessel_by_mmsi(mmsi: str) -> dict[str, Any] | None:
    """Get current position and details for a vessel by MMSI."""
    api_key = getattr(config, "datalastic_api_key", None)
    if api_key:
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

    # Fixture fallback (MMSI lookup)
    fixtures = _load_fixtures()
    fixture_hits = _find_in_fixtures(mmsi, fixtures)
    if fixture_hits:
        return fixture_hits[0]

    return None


async def vessel_by_imo(imo: str) -> dict[str, Any] | None:
    """Get current details for a vessel by IMO number."""
    api_key = getattr(config, "datalastic_api_key", None)
    if api_key:
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

    # OpenSanctions IMO lookup
    os_result = await vessel_find_by_imo_opensanctions(imo)
    if os_result:
        return os_result

    # Fixture fallback
    fixtures = _load_fixtures()
    fixture_hits = _find_in_fixtures(imo, fixtures)
    if fixture_hits:
        return fixture_hits[0]

    return None


async def vessel_history(mmsi: str, days: int = 14) -> list[dict[str, Any]]:
    """Fetch AIS position history for the last *days* days.

    Only Datalastic provides real history; without a key, returns empty list
    (route history is not available from OpenSanctions or fixtures).
    """
    api_key = getattr(config, "datalastic_api_key", None)
    if not api_key:
        return []

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
        return []


