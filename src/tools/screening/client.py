"""Async client for the Trade.gov Consolidated Screening List (CSL) API."""

from __future__ import annotations

import logging
from typing import Any

from ...common.cache import get_cached, set_cached
from ...common.config import config
from ...common.http_client import fetch_json

logger = logging.getLogger(__name__)

CSL_BASE_URL = "https://api.trade.gov/gateway/v1/consolidated_screening_list/search"
_CACHE_NS = "trade_gov_csl"
_CACHE_TTL = 3600


async def search_csl(query: str, limit: int = 25) -> list[dict[str, Any]]:
    """Search the Trade.gov Consolidated Screening List.

    NOTE: The Trade.gov gateway API now requires a subscription key.
    Returns empty list silently if TRADE_GOV_API_KEY is not configured.
    """
    if not config.trade_gov_api_key:
        logger.debug("Trade.gov CSL disabled (no TRADE_GOV_API_KEY)")
        return []

    cached = get_cached(_CACHE_NS, action="search", q=query, limit=limit)
    if cached is not None:
        return cached

    try:
        data = await fetch_json(
            CSL_BASE_URL,
            params={"q": query, "limit": limit},
            headers={"subscription-key": config.trade_gov_api_key},
        )
    except Exception as exc:
        logger.warning("Trade.gov CSL unavailable for query=%s: %s", query, exc)
        return []

    results: list[dict[str, Any]] = []
    for hit in data.get("results", []):
        results.append({
            "name": hit.get("name", ""),
            "source": hit.get("source", ""),
            "programs": hit.get("programs", []),
            "start_date": hit.get("start_date"),
            "end_date": hit.get("end_date"),
            "remarks": hit.get("remarks"),
            "source_list_url": hit.get("source_list_url"),
            "addresses": hit.get("addresses", []),
            "alt_names": hit.get("alt_names", []),
            "ids": hit.get("ids", []),
            "entity_number": hit.get("entity_number"),
            "type": hit.get("type"),
        })

    set_cached(results, _CACHE_NS, ttl=_CACHE_TTL, action="search", q=query, limit=limit)
    return results
