"""Async API clients for GDELT 2.0 and ACLED geopolitical data sources."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any

from ...common.cache import get_cached, set_cached
from ...common.config import config
from ...common.http_client import fetch_json
from .models import AcledEvent, GdeltEvent

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# GDELT 2.0 Client
# ---------------------------------------------------------------------------

GDELT_BASE = "https://api.gdeltproject.org/api/v2"
GDELT_DOC_URL = f"{GDELT_BASE}/doc/doc"
GDELT_GEO_URL = f"{GDELT_BASE}/geo/geo"

# Cache TTL: 30 minutes for GDELT (high-frequency updates)
GDELT_CACHE_TTL = 1800


def _gdelt_date_range(days: int) -> str:
    """Return a GDELT-compatible date range string for the last N days."""
    end = datetime.utcnow()
    start = end - timedelta(days=days)
    return f"{start.strftime('%Y%m%d%H%M%S')}-{end.strftime('%Y%m%d%H%M%S')}"


def _parse_gdelt_article(article: dict[str, Any]) -> GdeltEvent:
    """Parse a single GDELT article/doc result into a GdeltEvent."""
    date_str = article.get("seendate", "")
    parsed_date = None
    if date_str:
        try:
            parsed_date = datetime.strptime(date_str[:8], "%Y%m%d")
        except (ValueError, IndexError):
            pass

    return GdeltEvent(
        event_id=article.get("url", article.get("title", ""))[:128],
        date=parsed_date,
        actor1_name=article.get("sourcecountry", ""),
        actor1_country=article.get("sourcecountry", ""),
        actor2_name="",
        actor2_country="",
        event_code="",
        goldstein_scale=article.get("tone", None),
        num_mentions=1,
        avg_tone=article.get("tone", None),
        source_url=article.get("url", ""),
    )


async def gdelt_doc_search(
    query: str, days: int = 30, max_records: int = 75
) -> list[GdeltEvent]:
    """Search GDELT Doc API for articles matching a query.

    Uses the artlist mode which returns individual articles.
    """
    cached = get_cached("gdelt_doc", query=query, days=days)
    if cached is not None:
        return [GdeltEvent(**e) for e in cached]

    params: dict[str, Any] = {
        "query": query,
        "mode": "artlist",
        "format": "json",
        "maxrecords": str(max_records),
        "timespan": f"{days}d",
    }

    try:
        data = await fetch_json(GDELT_DOC_URL, params=params)
    except Exception as exc:
        logger.warning("GDELT doc search failed for query=%r: %s", query, exc)
        return []

    articles = data.get("articles", [])
    events = [_parse_gdelt_article(a) for a in articles]

    set_cached(
        [e.model_dump(mode="json") for e in events],
        "gdelt_doc",
        ttl=GDELT_CACHE_TTL,
        query=query,
        days=days,
    )
    return events


async def gdelt_geo_search(query: str, days: int = 30) -> list[dict[str, Any]]:
    """Search GDELT Geo API for geolocated events.

    Returns raw GeoJSON features.
    """
    cached = get_cached("gdelt_geo", query=query, days=days)
    if cached is not None:
        return cached

    params: dict[str, Any] = {
        "query": query,
        "format": "geojson",
        "timespan": f"{days}d",
    }

    try:
        data = await fetch_json(GDELT_GEO_URL, params=params)
    except Exception as exc:
        logger.warning("GDELT geo search failed for query=%r: %s", query, exc)
        return []

    features = data.get("features", [])

    set_cached(features, "gdelt_geo", ttl=GDELT_CACHE_TTL, query=query, days=days)
    return features


async def gdelt_timeline(query: str, days: int = 180) -> list[dict[str, Any]]:
    """Get a timeline of event volume from GDELT.

    Returns a list of {date, count} data points.
    """
    cached = get_cached("gdelt_timeline", query=query, days=days)
    if cached is not None:
        return cached

    params: dict[str, Any] = {
        "query": query,
        "mode": "timelinevol",
        "format": "json",
        "timespan": f"{days}d",
    }

    try:
        data = await fetch_json(GDELT_DOC_URL, params=params)
    except Exception as exc:
        logger.warning("GDELT timeline failed for query=%r: %s", query, exc)
        return []

    # GDELT timeline returns {"timeline": [{"series": [...], "data": [...]}]}
    timeline_series = data.get("timeline", [])
    data_points: list[dict[str, Any]] = []

    for series in timeline_series:
        for point in series.get("data", []):
            date_val = point.get("date", "")
            value = point.get("value", 0)
            data_points.append({"date": date_val, "count": value})

    set_cached(
        data_points, "gdelt_timeline", ttl=GDELT_CACHE_TTL, query=query, days=days
    )
    return data_points


async def gdelt_bilateral_search(
    country1: str, country2: str, days: int = 90, max_records: int = 75
) -> list[GdeltEvent]:
    """Search GDELT for events involving two specific countries."""
    query = f'("{country1}" AND "{country2}")'
    return await gdelt_doc_search(query, days=days, max_records=max_records)


# ---------------------------------------------------------------------------
# ACLED Client
# ---------------------------------------------------------------------------

ACLED_BASE = "https://api.acleddata.com/acled/read"
ACLED_TOKEN_URL = "https://acleddata.com/oauth/token"

# Cache TTL: 1 hour for ACLED (daily updates)
ACLED_CACHE_TTL = 3600


async def refresh_acled_token() -> bool:
    """Exchange the refresh token for a new ACLED access token.

    Updates config.acled_api_key in place. Returns True on success.
    """
    if not config.acled_refresh_token:
        logger.warning("ACLED refresh token not configured; skipping token refresh")
        return False

    import httpx
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(
                ACLED_TOKEN_URL,
                data={
                    "refresh_token": config.acled_refresh_token,
                    "grant_type": "refresh_token",
                    "client_id": "acled",
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            resp.raise_for_status()
            data = resp.json()
        new_token = data.get("access_token") or data.get("token")
        if not new_token:
            logger.error("ACLED token refresh returned no access_token: %s", data)
            return False
        config.acled_api_key = new_token
        logger.info("ACLED access token refreshed successfully")
        return True
    except Exception as exc:
        logger.error("ACLED token refresh failed: %s", exc)
        return False


def _acled_available() -> bool:
    """Check if ACLED credentials are configured."""
    return bool(config.acled_api_key and config.acled_email)


def _parse_acled_event(raw: dict[str, Any]) -> AcledEvent:
    """Parse a single ACLED API result into an AcledEvent."""
    event_date = None
    date_str = raw.get("event_date", "")
    if date_str:
        try:
            event_date = datetime.strptime(date_str, "%Y-%m-%d")
        except ValueError:
            pass

    return AcledEvent(
        event_id=str(raw.get("event_id_cnty", raw.get("data_id", ""))),
        event_date=event_date,
        event_type=raw.get("event_type", ""),
        sub_event_type=raw.get("sub_event_type", ""),
        actor1=raw.get("actor1", ""),
        actor2=raw.get("actor2", ""),
        country=raw.get("country", ""),
        location=raw.get("location", ""),
        fatalities=int(raw.get("fatalities", 0)),
        notes=raw.get("notes", ""),
        source=raw.get("source", ""),
    )


async def acled_get_events(
    country: str,
    days: int = 90,
    event_type: str | None = None,
    limit: int = 500,
) -> list[AcledEvent]:
    """Fetch conflict events from ACLED for a specific country.

    Returns an empty list if ACLED credentials are not configured.
    """
    if not _acled_available():
        logger.info("ACLED credentials not configured; skipping ACLED query")
        return []

    cached = get_cached(
        "acled_events", country=country, days=days, event_type=event_type or ""
    )
    if cached is not None:
        return [AcledEvent(**e) for e in cached]

    end_date = datetime.utcnow()
    start_date = end_date - timedelta(days=days)
    date_range = f"{start_date.strftime('%Y-%m-%d')}|{end_date.strftime('%Y-%m-%d')}"

    params: dict[str, Any] = {
        "key": config.acled_api_key,
        "email": config.acled_email,
        "country": country,
        "event_date": date_range,
        "event_date_where": "BETWEEN",
        "limit": str(limit),
    }
    if event_type:
        params["event_type"] = event_type

    try:
        data = await fetch_json(ACLED_BASE, params=params)
    except Exception as exc:
        logger.warning("ACLED query failed for country=%r: %s", country, exc)
        return []

    raw_events = data.get("data", [])
    events = [_parse_acled_event(e) for e in raw_events]

    set_cached(
        [e.model_dump(mode="json") for e in events],
        "acled_events",
        ttl=ACLED_CACHE_TTL,
        country=country,
        days=days,
        event_type=event_type or "",
    )
    return events


async def acled_get_events_bilateral(
    country: str,
    actor_filter: str,
    days: int = 90,
    limit: int = 500,
) -> list[AcledEvent]:
    """Fetch ACLED events filtered by actor name within a country.

    Returns an empty list if ACLED credentials are not configured.
    """
    if not _acled_available():
        return []

    cached = get_cached(
        "acled_bilateral", country=country, actor=actor_filter, days=days
    )
    if cached is not None:
        return [AcledEvent(**e) for e in cached]

    end_date = datetime.utcnow()
    start_date = end_date - timedelta(days=days)
    date_range = f"{start_date.strftime('%Y-%m-%d')}|{end_date.strftime('%Y-%m-%d')}"

    params: dict[str, Any] = {
        "key": config.acled_api_key,
        "email": config.acled_email,
        "country": country,
        "actor1": actor_filter,
        "event_date": date_range,
        "event_date_where": "BETWEEN",
        "limit": str(limit),
    }

    try:
        data = await fetch_json(ACLED_BASE, params=params)
    except Exception as exc:
        logger.warning("ACLED bilateral query failed: %s", exc)
        return []

    raw_events = data.get("data", [])
    events = [_parse_acled_event(e) for e in raw_events]

    set_cached(
        [e.model_dump(mode="json") for e in events],
        "acled_bilateral",
        ttl=ACLED_CACHE_TTL,
        country=country,
        actor=actor_filter,
        days=days,
    )
    return events


def is_acled_available() -> bool:
    """Public check for whether ACLED data source is available."""
    return _acled_available()
