"""Vessel-tracking client backed by OpenSanctions + a curated fixture.

OpenSanctions vessel-schema gives queryable particulars + owner/sanctions
data for *sanctioned* vessels. Clean commercial vessels (Maersk, COSCO,
Maran, …) aren't on OpenSanctions, so we fall back to
``data/fixtures/vessels.json`` for the demo's curated set.

Live AIS positions (used by ``vessel_history`` / ``vessel_port_calls``) come
from the wargame_backend ``ais_positions`` table, populated by the AISStream
ingest adapter. When that buffer isn't reachable (DB down, WARGAME disabled)
the position queries degrade gracefully to an empty result.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from src.tools.sanctions.client import (
    vessel_by_imo_opensanctions,
    vessel_by_mmsi_opensanctions,
    vessel_find_opensanctions,
)

logger = logging.getLogger(__name__)

# Co-located with the consumer so the file ships with the deploy. The previous
# location at <repo>/data/fixtures/vessels.json was being eaten by Render's
# runtime data/ directory (only data/cache/ + data/emissary.db are present
# at runtime), so the fixture fallback never had a file to read.
_FIXTURE_PATH = Path(__file__).parent / "fixtures" / "vessels.json"

_SOURCE_OPENSANCTIONS = "OpenSanctions Vessels"
_SOURCE_FIXTURE = "fixture"

_fixture_cache: list[dict[str, Any]] | None = None


def _load_fixtures() -> list[dict[str, Any]]:
    """Read the curated vessel fixture set, caching the parsed JSON in memory."""
    global _fixture_cache
    if _fixture_cache is not None:
        return _fixture_cache
    try:
        with _FIXTURE_PATH.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
            _fixture_cache = data if isinstance(data, list) else []
    except (FileNotFoundError, json.JSONDecodeError) as exc:
        logger.warning("vessel fixture unavailable (%s): %s", _FIXTURE_PATH, exc)
        _fixture_cache = []
    return _fixture_cache


def _first(values: Any) -> Any:
    """Return the first element of a list-ish value, or the value itself."""
    if isinstance(values, list):
        return values[0] if values else None
    return values


def _to_int(value: Any, default: int = 0) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _normalize_vessel(raw: dict[str, Any]) -> dict[str, Any]:
    """Map an OpenSanctions vessel entity to the canonical vessel shape.

    Position fields are zeroed — OpenSanctions has no live AIS. The
    ``status`` field surfaces sanctions status as a useful demo signal.
    """
    if not raw:
        return {}

    props = raw.get("properties") or {}
    caption = raw.get("caption") or _first(props.get("name")) or "Unknown"
    owner_raw = _first(props.get("owner"))
    operator_raw = _first(props.get("operator"))
    # Owner can be either an entity ID string or a richer dict; pick a name.
    if isinstance(owner_raw, dict):
        owner = owner_raw.get("caption") or owner_raw.get("name") or ""
    else:
        owner = owner_raw or ""
    sanctioned = bool(_first(props.get("sanctionedVessel"))) or any(
        ds for ds in (raw.get("datasets") or []) if "ofac" in str(ds).lower()
    )

    return {
        "name": caption,
        "imo": _first(props.get("imoNumber")) or "",
        "mmsi": _first(props.get("mmsi")) or "",
        "callsign": _first(props.get("callSign")) or "",
        "flag": _first(props.get("flag")) or "",
        "vessel_type": _first(props.get("type")) or _first(props.get("shipType")) or "",
        "length": _to_float(_first(props.get("lengthMeters")), default=0.0) or None,
        "width": _to_float(_first(props.get("widthMeters")), default=0.0) or None,
        "deadweight": _to_int(_first(props.get("deadweightTonnage")), default=0),
        "latitude": 0.0,
        "longitude": 0.0,
        "speed": 0.0,
        "course": 0,
        "heading": 0,
        "status": "Sanctioned" if sanctioned else "Unknown",
        "destination": "",
        "eta": "",
        "last_position_epoch": 0,
        "source": _SOURCE_OPENSANCTIONS,
        "owner": owner,
        "operator": operator_raw or "",
        "sanctioned": sanctioned,
        "opensanctions_id": raw.get("id") or "",
    }


def _normalize_fixture(entry: dict[str, Any]) -> dict[str, Any]:
    """Pass through a fixture row, filling any missing canonical keys with zeros."""
    base = {
        "name": "Unknown",
        "imo": "",
        "mmsi": "",
        "callsign": "",
        "flag": "",
        "vessel_type": "",
        "length": None,
        "width": None,
        "deadweight": 0,
        "latitude": 0.0,
        "longitude": 0.0,
        "speed": 0.0,
        "course": 0,
        "heading": 0,
        "status": "Unknown",
        "destination": "",
        "eta": "",
        "last_position_epoch": 0,
        "source": _SOURCE_FIXTURE,
        "owner": "",
        "operator": "",
        "sanctioned": False,
        "opensanctions_id": "",
    }
    base.update(entry)
    if base.get("source") == "fixture":
        base["source"] = _SOURCE_FIXTURE
    return base


def _fixture_search_by_name(query: str) -> list[dict[str, Any]]:
    """Return fixture entries whose name contains *query* (case-insensitive)."""
    q = (query or "").strip().lower()
    if not q:
        return []
    return [_normalize_fixture(v) for v in _load_fixtures() if q in (v.get("name") or "").lower()]


def _fixture_lookup(field: str, value: str) -> dict[str, Any] | None:
    """Return the first fixture entry whose *field* equals *value* (string match)."""
    target = str(value).strip()
    if not target:
        return None
    for v in _load_fixtures():
        if str(v.get(field) or "").strip() == target:
            return _normalize_fixture(v)
    return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def _vessel_name_matches(query: str, vessel: dict[str, Any]) -> bool:
    """True iff a significant token from *query* appears in the vessel's
    name field. Mirrors :func:`_ofac_hit_matches_company_label` in api.py.

    Without this filter, OpenSanctions' fuzzy ``/search/default`` ranker
    can surface a high-profile sanctioned tanker (e.g. HATTI) at the top
    of results for an unrelated query like "ever given" -- it scores a
    match somewhere in the indexed text (notes, owner, designations)
    rather than the actual name. Requiring name-token overlap forces a
    real lexical match before we accept the hit.
    """
    tokens = [t for t in re.findall(r"[a-z0-9]+", (query or "").lower()) if len(t) >= 2]
    if not tokens:
        return False
    significant = [t for t in tokens if len(t) >= 4] or tokens
    name_text = (vessel.get("name") or "").lower()
    name_tokens = set(re.findall(r"[a-z0-9]+", name_text))
    return any(t in name_tokens for t in significant)


async def vessel_find(name: str) -> list[dict[str, Any]]:
    """Search for vessels by name. Fixture wins; OpenSanctions is supplemental.

    Design intent (per Option A): the curated fixture set is the source of
    truth for known commercial vessels (EVER GIVEN, COSCO, Maersk, ...). Only
    when the query doesn't match anything in the fixture do we consult
    OpenSanctions, which adds coverage for *sanctioned* vessels that aren't
    in our demo set (AKIN HALAY-style entries).

    Even on the OS leg, results are filtered two ways:
      - must have IMO or MMSI (drop caption-only stubs)
      - must have a name-token overlap with the query (drop the HATTI-style
        false matches where OS' fuzzy ranker returns an unrelated vessel
        because it scored on notes/owner/designation text)
    """
    if not name:
        return []

    # Option A: fixture wins outright when it has a match for this name.
    fixture_results = _fixture_search_by_name(name)
    if fixture_results:
        return fixture_results

    try:
        raw_results = await vessel_find_opensanctions(name)
    except Exception as exc:
        logger.warning("vessel_find OpenSanctions error: %s", exc)
        raw_results = []

    normalized = [_normalize_vessel(r) for r in raw_results if r]
    useful = [
        n
        for n in normalized
        if n and (n.get("imo") or n.get("mmsi")) and _vessel_name_matches(name, n)
    ]
    return useful


async def vessel_by_mmsi(mmsi: str) -> dict[str, Any] | None:
    """Resolve a vessel by MMSI via OpenSanctions, falling back to fixtures."""
    if not mmsi:
        return None
    try:
        raw = await vessel_by_mmsi_opensanctions(mmsi)
    except Exception as exc:
        logger.warning("vessel_by_mmsi OpenSanctions error: %s", exc)
        raw = None
    if raw:
        return _normalize_vessel(raw)
    return _fixture_lookup("mmsi", mmsi)


async def vessel_by_imo(imo: str) -> dict[str, Any] | None:
    """Resolve a vessel by IMO via OpenSanctions, falling back to fixtures."""
    if not imo:
        return None
    try:
        raw = await vessel_by_imo_opensanctions(imo)
    except Exception as exc:
        logger.warning("vessel_by_imo OpenSanctions error: %s", exc)
        raw = None
    if raw:
        return _normalize_vessel(raw)
    return _fixture_lookup("imo", imo)


async def vessel_history(mmsi: str, days: int = 30) -> list[dict[str, Any]]:
    """Read AIS position history for *mmsi* from the ais_positions buffer.

    Lazy-imports the wargame_backend DB helpers so this module stays
    importable when the wargame backend isn't mounted or its DB connection
    isn't configured. Returns ``[]`` on any error.
    """
    if not mmsi:
        return []
    try:
        from wargame_backend.app.db.ais_positions import read_positions_for_mmsi
        from wargame_backend.app.db.session import AsyncSessionLocal
    except ImportError:
        logger.info("vessel_history: wargame_backend not available")
        return []

    since = datetime.now(timezone.utc) - timedelta(days=days)
    try:
        async with AsyncSessionLocal() as session:
            return await read_positions_for_mmsi(session, mmsi, since)
    except Exception as exc:  # noqa: BLE001
        logger.warning("vessel_history DB error: %s", exc)
        return []


async def vessel_port_calls(mmsi: str, days: int = 90) -> list[dict[str, Any]]:
    """Infer port calls from the position buffer via ``infer_port_stops()``."""
    positions = await vessel_history(mmsi, days=days)
    return infer_port_stops(positions)


# ---------------------------------------------------------------------------
# Position normalization / port-stop inference — provider-agnostic geometry
# ---------------------------------------------------------------------------


def _normalize_position(raw: dict[str, Any]) -> dict[str, Any]:
    """Normalize a single AIS position point — kept for downstream callers."""
    return {
        "latitude": raw.get("lat") or raw.get("latitude") or 0.0,
        "longitude": raw.get("lon") or raw.get("longitude") or 0.0,
        "speed": raw.get("speed") or 0.0,
        "course": raw.get("course") or 0,
        "timestamp": raw.get("last_position_epoch") or raw.get("timestamp") or 0,
    }


def infer_port_stops(
    positions: list[dict[str, Any]], speed_threshold: float = 1.0
) -> list[dict[str, Any]]:
    """Infer port stops from AIS position history by detecting low-speed clusters.

    Groups consecutive positions where speed < threshold into stops, returning
    unique stops deduplicated by proximity (within ~10 km).
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

    unique: list[dict[str, Any]] = []
    for stop in stops:
        is_dup = False
        for u in unique:
            if (
                abs(stop["latitude"] - u["latitude"]) < 0.1
                and abs(stop["longitude"] - u["longitude"]) < 0.1
            ):
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
