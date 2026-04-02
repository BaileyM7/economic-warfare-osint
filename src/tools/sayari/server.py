"""MCP-style tools that expose Sayari capabilities to the orchestrator."""

from __future__ import annotations

import logging

from src.tools.sayari.rest_client import get_sayari_client

logger = logging.getLogger(__name__)


async def sayari_resolve(query: str, limit: int = 5) -> dict:
    """Resolve an entity name to Sayari entity IDs with metadata."""
    client = get_sayari_client()
    try:
        result = await client.resolve(query, limit=limit)
        return result.model_dump(mode="json")
    except Exception as exc:
        logger.warning("sayari_resolve failed for %s: %s", query, exc)
        return {"error": str(exc), "entities": [], "query": query}


async def sayari_get_related(entity_id: str, depth: int = 1, limit: int = 20) -> dict:
    """Get entities related to a Sayari entity (graph traversal)."""
    client = get_sayari_client()
    try:
        result = await client.get_traversal(entity_id, depth=depth, limit=limit)
        return result.model_dump(mode="json")
    except Exception as exc:
        logger.warning("sayari_get_related failed for %s: %s", entity_id, exc)
        return {"error": str(exc), "root_id": entity_id, "entities": [], "relationships": []}


async def sayari_get_ubo(entity_id: str) -> dict:
    """Get the ultimate beneficial owners of a Sayari entity."""
    client = get_sayari_client()
    try:
        result = await client.get_ubo(entity_id)
        return result.model_dump(mode="json")
    except Exception as exc:
        logger.warning("sayari_get_ubo failed for %s: %s", entity_id, exc)
        return {"error": str(exc), "target_id": entity_id, "target_name": "", "owners": []}


async def sayari_get_entity(entity_id: str) -> dict:
    """Fetch the full profile of a single Sayari entity."""
    client = get_sayari_client()
    try:
        result = await client.get_entity(entity_id)
        return result.model_dump(mode="json")
    except Exception as exc:
        logger.warning("sayari_get_entity failed for %s: %s", entity_id, exc)
        return {"error": str(exc), "entity_id": entity_id}
