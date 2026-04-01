"""Lightweight reverse geocoding for maritime coordinates.

Uses BigDataCloud free API (no key required, 50k/month) to resolve
lat/lon to country. Falls back to ocean region estimation.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from src.common.cache import get_cached, set_cached
from src.common.http_client import fetch_json

logger = logging.getLogger(__name__)

_CACHE_NS = "reverse_geo"
_CACHE_TTL = 86400 * 7  # 7 days — countries don't move


async def reverse_geocode(lat: float, lon: float) -> dict[str, str]:
    """Resolve lat/lon to country name and code.

    Returns {"country": "Japan", "country_code": "JP"} or ocean region fallback.
    """
    # Round to 2 decimals for cache efficiency (~1km precision, fine for country)
    rlat = round(lat, 2)
    rlon = round(lon, 2)

    cached = get_cached(_CACHE_NS, lat=rlat, lon=rlon)
    if cached is not None:
        return cached

    try:
        data = await fetch_json(
            "https://api.bigdatacloud.net/data/reverse-geocode-client",
            params={"latitude": rlat, "longitude": rlon, "localityLanguage": "en"},
            timeout=5.0,
        )
        country_name = data.get("countryName", "") if isinstance(data, dict) else ""
        if country_name:
            result = {
                "country": country_name,
                "country_code": data.get("countryCode", ""),
            }
        else:
            result = _ocean_region(lat, lon)
        set_cached(result, _CACHE_NS, lat=rlat, lon=rlon, ttl=_CACHE_TTL)
        return result
    except Exception:
        result = _ocean_region(lat, lon)
        set_cached(result, _CACHE_NS, lat=rlat, lon=rlon, ttl=_CACHE_TTL)
        return result


async def batch_reverse_geocode(
    coordinates: list[tuple[float, float]],
    max_concurrent: int = 5,
) -> list[dict[str, str]]:
    """Reverse geocode multiple coordinates with concurrency limit."""
    sem = asyncio.Semaphore(max_concurrent)

    async def _limited(lat: float, lon: float) -> dict[str, str]:
        async with sem:
            return await reverse_geocode(lat, lon)

    return await asyncio.gather(*[_limited(lat, lon) for lat, lon in coordinates])


async def get_countries_from_positions(
    positions: list[dict[str, Any]],
    sample_size: int = 8,
) -> list[str]:
    """Extract unique country names from AIS positions.

    Samples positions evenly across the route to avoid excessive API calls.
    """
    if not positions:
        return []

    # Sample evenly across the route
    step = max(1, len(positions) // sample_size)
    sampled = positions[::step][:sample_size]

    coords = [(p["latitude"], p["longitude"]) for p in sampled
              if p.get("latitude") and p.get("longitude")]
    if not coords:
        return []

    results = await batch_reverse_geocode(coords)
    # Separate real countries from ocean regions
    countries: list[str] = []
    regions: list[str] = []
    seen: set[str] = set()
    for r in results:
        name = r.get("country", "")
        if not name or name in seen:
            continue
        seen.add(name)
        if name.startswith("("):
            regions.append(name)
        else:
            countries.append(name)
    # Countries first, then ocean regions
    return countries + regions


def _ocean_region(lat: float, lon: float) -> dict[str, str]:
    """Estimate ocean region from coordinates when no country is found."""
    if -30 <= lat <= 30 and 25 <= lon <= 100:
        region = "(Indian Ocean)"
    elif -60 <= lat <= 60 and 100 <= lon <= 180:
        region = "(Pacific Ocean - West)"
    elif -60 <= lat <= 60 and -180 <= lon <= -80:
        region = "(Pacific Ocean - East)"
    elif -60 <= lat <= 60 and -80 <= lon <= 0:
        region = "(Atlantic Ocean - West)"
    elif -60 <= lat <= 60 and 0 <= lon <= 25:
        region = "(Atlantic Ocean - East)"
    elif 30 <= lat <= 45 and 25 <= lon <= 45:
        region = "(Mediterranean/Red Sea)"
    elif lat > 60:
        region = "(Arctic)"
    elif lat < -60:
        region = "(Southern Ocean)"
    else:
        region = "(Open Ocean)"
    return {"country": region, "country_code": ""}
