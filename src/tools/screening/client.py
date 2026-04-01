"""Async client for the Trade.gov Consolidated Screening List (CSL) API.

Endpoint reference (updated Dec 2021+):
  https://data.trade.gov/consolidated_screening_list/v1/search

Requires a free API key from https://developer.trade.gov  — set TRADE_GOV_API_KEY.
"""

from __future__ import annotations

import logging
from typing import Any

from ...common.cache import get_cached, set_cached
from ...common.config import config
from ...common.http_client import fetch_json

logger = logging.getLogger(__name__)

CSL_BASE_URL = "https://data.trade.gov/consolidated_screening_list/v1/search"
CSL_SOURCES_URL = "https://data.trade.gov/consolidated_screening_list/v1/sources"
_CACHE_NS = "trade_gov_csl"
_CACHE_TTL = 3600


async def search_csl(
    query: str,
    limit: int = 25,
    sources: str = "",
    countries: str = "",
) -> list[dict[str, Any]]:
    """Search the Trade.gov Consolidated Screening List.

    Parameters
    ----------
    query : name to search (the API does its own fuzzy matching)
    limit : max results (API default 25)
    sources : comma-separated source filter (e.g. "SDN,Entity List")
    countries : comma-separated country filter
    """
    if not config.trade_gov_api_key:
        logger.warning(
            "Trade.gov CSL disabled — set TRADE_GOV_API_KEY in .env "
            "(free key from https://developer.trade.gov/)"
        )
        return []

    cached = get_cached(
        _CACHE_NS, action="search", q=query, limit=limit,
        sources=sources, countries=countries,
    )
    if cached is not None:
        return cached

    params: dict[str, Any] = {"name": query, "limit": limit}
    if sources:
        params["sources"] = sources
    if countries:
        params["countries"] = countries

    try:
        data = await fetch_json(
            CSL_BASE_URL,
            params=params,
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

    set_cached(
        results, _CACHE_NS, ttl=_CACHE_TTL, action="search", q=query,
        limit=limit, sources=sources, countries=countries,
    )
    return results


async def get_csl_sources() -> list[dict[str, Any]]:
    """Return the list of screening-list sources available in the CSL."""
    cached = get_cached(_CACHE_NS, action="sources")
    if cached is not None:
        return cached

    if not config.trade_gov_api_key:
        return []

    try:
        data = await fetch_json(
            CSL_SOURCES_URL,
            headers={"subscription-key": config.trade_gov_api_key},
        )
    except Exception as exc:
        logger.warning("Trade.gov CSL sources unavailable: %s", exc)
        return []

    sources = data.get("sources", data if isinstance(data, list) else [])
    set_cached(sources, _CACHE_NS, ttl=86400, action="sources")
    return sources
