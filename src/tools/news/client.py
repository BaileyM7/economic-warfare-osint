"""GDELT 'recent news' client — raw article HEADLINES (title + url + source + date).

Distinct from src/tools/geopolitical (which returns deduplicated GDELT *events*):
this returns individual recent ARTICLES for a demo-friendly "latest headlines"
agent. Free GDELT 2.0 Doc API — no API key.
"""

from __future__ import annotations

import logging
from typing import Any

from ...common.cache import get_cached, set_cached
from ...common.http_client import fetch_json

logger = logging.getLogger(__name__)

GDELT_DOC_URL = "https://api.gdeltproject.org/api/v2/doc/doc"
_CACHE_NS = "gdelt_news"
_CACHE_TTL = 1800  # 30 min — news moves fast, but not second-by-second


async def search_news_articles(query: str, days: int = 14, limit: int = 20) -> list[dict[str, Any]]:
    """Recent news articles matching *query* (title, url, source, date), newest first."""
    limit = min(max(limit, 1), 75)
    cached = get_cached(_CACHE_NS, q=query, days=days, limit=limit)
    if cached is not None:
        return cached

    params = {
        "query": query,
        "mode": "artlist",
        "format": "json",
        "maxrecords": str(limit),
        "timespan": f"{days}d",
        "sort": "datedesc",
    }
    try:
        data = await fetch_json(GDELT_DOC_URL, params=params)
    except Exception as exc:
        logger.warning("GDELT news search failed for %r: %s", query, exc)
        return []

    out: list[dict[str, Any]] = []
    for a in (data.get("articles") or [])[:limit]:
        title = (a.get("title") or "").strip()
        url = a.get("url") or ""
        if not title or not url:
            continue
        out.append(
            {
                "title": title,
                "url": url,
                "source": a.get("domain") or a.get("sourcecountry") or "",
                "date": a.get("seendate") or "",
                "language": a.get("language") or "",
            }
        )

    set_cached(out, _CACHE_NS, ttl=_CACHE_TTL, q=query, days=days, limit=limit)
    return out
