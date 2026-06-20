"""News tool — recent headlines for an entity/topic via GDELT (free, no key).

Adds a distinct "news" agent to the orchestrator swarm. Returns the standard
ToolResponse envelope like every other tool.
"""

from __future__ import annotations

import logging

from ...common.types import Confidence, SourceReference, ToolResponse
from .client import search_news_articles

logger = logging.getLogger(__name__)


async def search_recent_news(query: str, days: int = 14) -> dict:
    """Recent news headlines (title + link + source + date) matching *query*.

    Distinct from geopolitical.search_events (deduplicated GDELT events): this
    surfaces individual recent ARTICLES — "what's the latest on X". Free GDELT
    Doc API, no key required.
    """
    errors: list[str] = []
    try:
        articles = await search_news_articles(query, days=days)
    except Exception as exc:
        logger.error("search_recent_news error: %s", exc)
        articles = []
        errors.append(str(exc))

    response = ToolResponse(
        data={
            "query": query,
            "days": days,
            "count": len(articles),
            "articles": articles,
        },
        confidence=Confidence.MEDIUM if len(articles) >= 5 else Confidence.LOW,
        sources=[
            SourceReference(
                name="GDELT 2.0 Doc API",
                url="https://www.gdeltproject.org/",
                description="Global news article index (free, no key)",
            )
        ],
        errors=errors,
    )
    return response.model_dump(mode="json")
