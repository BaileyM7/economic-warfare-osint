"""MCP server exposing sanctions and watchlist search tools."""

from __future__ import annotations

import logging
from datetime import datetime

from mcp.server.fastmcp import FastMCP

from src.common.types import Confidence, SourceReference, ToolResponse

from .client import SanctionsClient

logger = logging.getLogger(__name__)

mcp = FastMCP("sanctions")

_client = SanctionsClient()


def _opensanctions_source() -> SourceReference:
    return SourceReference(
        name="OpenSanctions",
        url="https://api.opensanctions.org/",
        accessed_at=datetime.utcnow(),
    )


def _ofac_source() -> SourceReference:
    return SourceReference(
        name="OFAC SDN",
        url="https://www.treasury.gov/ofac/downloads/",
        accessed_at=datetime.utcnow(),
    )


def _determine_confidence(match_count: int, top_score: float | None) -> Confidence:
    """Heuristic confidence based on match quality."""
    if match_count == 0:
        return Confidence.LOW
    if top_score is not None and top_score >= 0.9:
        return Confidence.HIGH
    if top_score is not None and top_score >= 0.6:
        return Confidence.MEDIUM
    return Confidence.LOW


@mcp.tool()
async def search_sanctions(query: str, entity_type: str = "any") -> dict:
    """Search across OpenSanctions and OFAC SDN for sanctioned entities.

    Args:
        query: Name or identifier to search for (person, company, vessel, etc.).
        entity_type: Filter by entity type — "person", "company", "vessel",
                     "aircraft", or "any" (default).

    Returns:
        ToolResponse with list of matching SanctionEntry records, confidence,
        and source provenance.
    """
    errors: list[str] = []
    try:
        result = await _client.search(query, entity_type=entity_type)
    except Exception as exc:
        logger.exception("search_sanctions failed for query=%s", query)
        errors.append(f"Search error: {exc}")
        response = ToolResponse(
            data={"query": query, "matches": [], "total_matches": 0},
            confidence=Confidence.LOW,
            sources=[_opensanctions_source(), _ofac_source()],
            errors=errors,
        )
        return response.model_dump(mode="json")

    top_score = result.matches[0].score if result.matches else None
    confidence = _determine_confidence(result.total_matches, top_score)

    response = ToolResponse(
        data=result.model_dump(mode="json"),
        confidence=confidence,
        sources=[_opensanctions_source(), _ofac_source()],
        errors=errors,
    )
    return response.model_dump(mode="json")


@mcp.tool()
async def check_sanctions_status(entity_name: str) -> dict:
    """Check if a specific entity is sanctioned and on which lists.

    Args:
        entity_name: The exact or approximate name of the entity to check.

    Returns:
        ToolResponse with SanctionStatus indicating whether the entity is
        sanctioned, which lists it appears on, designation dates, and programs.
    """
    errors: list[str] = []
    try:
        status = await _client.check_status(entity_name)
    except Exception as exc:
        logger.exception("check_sanctions_status failed for entity=%s", entity_name)
        errors.append(f"Status check error: {exc}")
        response = ToolResponse(
            data={
                "entity_name": entity_name,
                "is_sanctioned": False,
                "lists_found": [],
                "programs": [],
                "entries": [],
            },
            confidence=Confidence.LOW,
            sources=[_opensanctions_source(), _ofac_source()],
            errors=errors,
        )
        return response.model_dump(mode="json")

    # Confidence is higher when we have definitive positive or negative results
    if status.is_sanctioned and len(status.entries) >= 2:
        confidence = Confidence.HIGH
    elif status.is_sanctioned:
        confidence = Confidence.MEDIUM
    else:
        # Absence of evidence is not evidence of absence — but it's our best info
        confidence = Confidence.MEDIUM

    response = ToolResponse(
        data=status.model_dump(mode="json"),
        confidence=confidence,
        sources=[_opensanctions_source(), _ofac_source()],
        errors=errors,
    )
    return response.model_dump(mode="json")


@mcp.tool()
async def get_sanctions_proximity(entity_name: str, max_hops: int = 3) -> dict:
    """Check degrees of separation between an entity and sanctioned entities.

    Walks the OpenSanctions entity relationship graph to find how many hops
    away the nearest sanctioned entity is.

    Args:
        entity_name: The entity to start the proximity search from.
        max_hops: Maximum number of relationship hops to traverse (1-5).
                  Higher values are slower. Default is 3.

    Returns:
        ToolResponse with ProximityResult showing the graph neighborhood
        and nearest sanctioned entities.
    """
    max_hops = max(1, min(max_hops, 5))

    errors: list[str] = []
    try:
        result = await _client.get_proximity(entity_name, max_hops=max_hops)
    except Exception as exc:
        logger.exception(
            "get_sanctions_proximity failed for entity=%s", entity_name
        )
        errors.append(f"Proximity search error: {exc}")
        response = ToolResponse(
            data={
                "query_entity": entity_name,
                "nodes": [],
                "edges": [],
                "nearest_sanctioned_hop": None,
                "sanctioned_neighbors": [],
            },
            confidence=Confidence.LOW,
            sources=[_opensanctions_source()],
            errors=errors,
        )
        return response.model_dump(mode="json")

    # Confidence based on graph completeness
    if result.nearest_sanctioned_hop is not None:
        if result.nearest_sanctioned_hop == 0:
            confidence = Confidence.HIGH  # Direct match
        elif len(result.nodes) > 3:
            confidence = Confidence.MEDIUM  # Reasonable graph coverage
        else:
            confidence = Confidence.LOW
    else:
        # No sanctioned neighbors found — could mean clean or incomplete graph
        confidence = Confidence.LOW if len(result.nodes) < 3 else Confidence.MEDIUM

    response = ToolResponse(
        data=result.model_dump(mode="json"),
        confidence=confidence,
        sources=[_opensanctions_source()],
        errors=errors,
    )
    return response.model_dump(mode="json")


@mcp.tool()
async def get_recent_designations(days: int = 30) -> dict:
    """Get recent OFAC sanctions designation actions.

    Scans the OFAC SDN list for entries with designation dates within the
    specified window. Note: date extraction is best-effort from the remarks
    field, so coverage may be incomplete.

    Args:
        days: Look back this many days from today. Default 30, max 365.

    Returns:
        ToolResponse with list of RecentDesignation records.
    """
    days = max(1, min(days, 365))

    errors: list[str] = []
    try:
        designations = await _client.get_recent_designations(days=days)
    except Exception as exc:
        logger.exception("get_recent_designations failed")
        errors.append(f"Recent designations error: {exc}")
        response = ToolResponse(
            data={"days": days, "designations": [], "count": 0},
            confidence=Confidence.LOW,
            sources=[_ofac_source()],
            errors=errors,
        )
        return response.model_dump(mode="json")

    # OFAC date extraction from remarks is inherently limited
    confidence = Confidence.MEDIUM if designations else Confidence.LOW

    response = ToolResponse(
        data={
            "days": days,
            "designations": [d.model_dump(mode="json") for d in designations],
            "count": len(designations),
        },
        confidence=confidence,
        sources=[_ofac_source()],
        errors=errors,
    )
    return response.model_dump(mode="json")


if __name__ == "__main__":
    mcp.run()
