"""MCP server for the Corporate Graph tool.

Exposes tools for searching companies, building ownership trees,
finding beneficial owners, and discovering offshore connections
across OpenCorporates, GLEIF, and ICIJ Offshore Leaks.

Run standalone:
    uv run python -m src.tools.corporate.server
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime

from mcp.server.fastmcp import FastMCP

from ...common.types import Confidence, SourceReference, ToolResponse
from . import client
from .models import (
    BeneficialOwnerResult,
    CorporateSearchResult,
    CorporateTree,
    OffshoreConnectionResult,
    OwnershipLink,
    ResolvedEntity,
)

logger = logging.getLogger(__name__)

mcp = FastMCP("corporate-graph")

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SRC_OC = SourceReference(name="OpenCorporates", url="https://opencorporates.com")
_SRC_GLEIF = SourceReference(name="GLEIF", url="https://www.gleif.org")
_SRC_ICIJ = SourceReference(name="ICIJ Offshore Leaks", url="https://offshoreleaks.icij.org")


def _assess_confidence(
    oc_count: int,
    gleif_count: int,
    icij_count: int,
) -> Confidence:
    """Heuristic confidence based on how many sources returned results."""
    total_sources = (1 if oc_count else 0) + (1 if gleif_count else 0) + (1 if icij_count else 0)
    total_records = oc_count + gleif_count + icij_count
    if total_sources >= 2 and total_records >= 3:
        return Confidence.HIGH
    if total_sources >= 1 and total_records >= 1:
        return Confidence.MEDIUM
    return Confidence.LOW


def _sources_used(oc: bool, gleif: bool, icij: bool) -> list[SourceReference]:
    sources: list[SourceReference] = []
    if oc:
        sources.append(_SRC_OC.model_copy(update={"accessed_at": datetime.utcnow()}))
    if gleif:
        sources.append(_SRC_GLEIF.model_copy(update={"accessed_at": datetime.utcnow()}))
    if icij:
        sources.append(_SRC_ICIJ.model_copy(update={"accessed_at": datetime.utcnow()}))
    return sources


# ---------------------------------------------------------------------------
# Tool: search_entity
# ---------------------------------------------------------------------------

@mcp.tool()
async def search_entity(query: str) -> dict:
    """Search for a company or entity across OpenCorporates, GLEIF, and ICIJ Offshore Leaks.

    Returns unified results from all three sources.
    """
    errors: list[str] = []

    oc_task = client.oc_search_companies(query)
    gleif_task = client.gleif_search_lei(query)
    icij_task = client.icij_search(query)

    oc_results, gleif_results, icij_results = await asyncio.gather(
        oc_task, gleif_task, icij_task, return_exceptions=True,
    )

    if isinstance(oc_results, BaseException):
        errors.append(f"OpenCorporates error: {oc_results}")
        oc_results = []
    if isinstance(gleif_results, BaseException):
        errors.append(f"GLEIF error: {gleif_results}")
        gleif_results = []
    if isinstance(icij_results, BaseException):
        errors.append(f"ICIJ error: {icij_results}")
        icij_results = []

    payload = CorporateSearchResult(
        companies=oc_results,
        lei_records=gleif_results,
        offshore_entities=icij_results,
    )

    confidence = _assess_confidence(len(oc_results), len(gleif_results), len(icij_results))
    sources = _sources_used(bool(oc_results), bool(gleif_results), bool(icij_results))

    return ToolResponse(
        data=payload.model_dump(mode="json"),
        confidence=confidence,
        sources=sources,
        errors=errors,
    ).model_dump(mode="json")


# ---------------------------------------------------------------------------
# Tool: get_corporate_tree
# ---------------------------------------------------------------------------

@mcp.tool()
async def get_corporate_tree(entity_name: str) -> dict:
    """Build an ownership chain (parent/child) for an entity using GLEIF and OpenCorporates.

    Resolves the entity to an LEI, then walks the parent hierarchy.
    """
    errors: list[str] = []
    ownership_links: list[OwnershipLink] = []
    all_lei_records = []
    all_companies = []

    # Step 1: resolve to LEI records
    lei_records = await client.gleif_search_lei(entity_name)
    all_lei_records.extend(lei_records)

    # Step 2: for each LEI, fetch parent relationships
    for rec in lei_records:
        direct_parent_task = client.gleif_get_direct_parent(rec.lei)
        ultimate_parent_task = client.gleif_get_ultimate_parent(rec.lei)
        direct_parent, ultimate_parent = await asyncio.gather(
            direct_parent_task, ultimate_parent_task, return_exceptions=True,
        )

        if isinstance(direct_parent, OwnershipLink) and direct_parent.parent_id:
            ownership_links.append(direct_parent)
            # Fetch the parent LEI record for context
            parent_rec = await client.gleif_get_lei_record(direct_parent.parent_id)
            if parent_rec:
                all_lei_records.append(parent_rec)
                rec.parent_lei = direct_parent.parent_id
        elif isinstance(direct_parent, BaseException):
            errors.append(f"Direct parent lookup error for {rec.lei}: {direct_parent}")

        if isinstance(ultimate_parent, OwnershipLink) and ultimate_parent.parent_id:
            # Only add if different from direct parent
            if not any(l.parent_id == ultimate_parent.parent_id and l.relationship_type == "ultimate_parent" for l in ownership_links):
                ownership_links.append(ultimate_parent)
                ult_rec = await client.gleif_get_lei_record(ultimate_parent.parent_id)
                if ult_rec:
                    all_lei_records.append(ult_rec)
                    rec.ultimate_parent_lei = ultimate_parent.parent_id
        elif isinstance(ultimate_parent, BaseException):
            errors.append(f"Ultimate parent lookup error for {rec.lei}: {ultimate_parent}")

    # Step 3: also search OpenCorporates for the entity
    companies = await client.oc_search_companies(entity_name)
    all_companies.extend(companies)

    # Fetch officers for top results (limit to 3 to respect rate limits)
    for comp in companies[:3]:
        if comp.jurisdiction and comp.company_number:
            detailed = await client.oc_get_company(comp.jurisdiction, comp.company_number)
            if detailed:
                idx = all_companies.index(comp)
                all_companies[idx] = detailed

    # Deduplicate LEI records by lei
    seen_leis: set[str] = set()
    unique_lei_records = []
    for rec in all_lei_records:
        if rec.lei not in seen_leis:
            seen_leis.add(rec.lei)
            unique_lei_records.append(rec)

    payload = CorporateTree(
        entity_name=entity_name,
        ownership_links=ownership_links,
        companies=all_companies,
        lei_records=unique_lei_records,
    )

    confidence = _assess_confidence(len(all_companies), len(unique_lei_records), 0)
    sources = _sources_used(bool(all_companies), bool(unique_lei_records), False)

    return ToolResponse(
        data=payload.model_dump(mode="json"),
        confidence=confidence,
        sources=sources,
        errors=errors,
    ).model_dump(mode="json")


# ---------------------------------------------------------------------------
# Tool: get_beneficial_owners
# ---------------------------------------------------------------------------

@mcp.tool()
async def get_beneficial_owners(entity_name: str) -> dict:
    """Find officers and ultimate beneficial owners for an entity.

    Searches OpenCorporates for officers and ICIJ for offshore connections.
    """
    errors: list[str] = []

    # Search across sources in parallel
    oc_companies_task = client.oc_search_companies(entity_name)
    oc_officers_task = client.oc_search_officers(entity_name)
    icij_task = client.icij_search(entity_name)

    oc_companies, oc_officers, icij_results = await asyncio.gather(
        oc_companies_task, oc_officers_task, icij_task, return_exceptions=True,
    )

    if isinstance(oc_companies, BaseException):
        errors.append(f"OpenCorporates company search error: {oc_companies}")
        oc_companies = []
    if isinstance(oc_officers, BaseException):
        errors.append(f"OpenCorporates officer search error: {oc_officers}")
        oc_officers = []
    if isinstance(icij_results, BaseException):
        errors.append(f"ICIJ search error: {icij_results}")
        icij_results = []

    # Fetch detailed officer info for top company matches
    all_officers = list(oc_officers)
    detailed_companies = list(oc_companies)
    for i, comp in enumerate(oc_companies[:3]):
        if comp.jurisdiction and comp.company_number:
            detailed = await client.oc_get_company(comp.jurisdiction, comp.company_number)
            if detailed:
                detailed_companies[i] = detailed
                for officer in detailed.officers:
                    if not any(o.name == officer.name and o.role == officer.role for o in all_officers):
                        all_officers.append(officer)

    payload = BeneficialOwnerResult(
        entity_name=entity_name,
        officers=all_officers,
        offshore_connections=icij_results,
        companies=detailed_companies,
    )

    confidence = _assess_confidence(
        len(detailed_companies) + len(all_officers),
        0,
        len(icij_results),
    )
    sources = _sources_used(
        bool(detailed_companies) or bool(all_officers),
        False,
        bool(icij_results),
    )

    return ToolResponse(
        data=payload.model_dump(mode="json"),
        confidence=confidence,
        sources=sources,
        errors=errors,
    ).model_dump(mode="json")


# ---------------------------------------------------------------------------
# Tool: get_offshore_connections
# ---------------------------------------------------------------------------

@mcp.tool()
async def get_offshore_connections(entity_name: str) -> dict:
    """Search the ICIJ Offshore Leaks database for hidden structures related to an entity.

    Covers Panama Papers, Paradise Papers, Pandora Papers, and other leaked datasets.
    """
    errors: list[str] = []

    # Search for entities and officers in ICIJ
    entity_task = client.icij_search(entity_name, entity_type="entity")
    officer_task = client.icij_search(entity_name, entity_type="officer")

    entity_results, officer_results = await asyncio.gather(
        entity_task, officer_task, return_exceptions=True,
    )

    if isinstance(entity_results, BaseException):
        errors.append(f"ICIJ entity search error: {entity_results}")
        entity_results = []
    if isinstance(officer_results, BaseException):
        errors.append(f"ICIJ officer search error: {officer_results}")
        officer_results = []

    all_entities = list(entity_results) + list(officer_results)

    # Fetch detailed node info for top results
    enriched: list = []
    seen_ids: set[str] = set()
    for ent in all_entities[:10]:
        if ent.node_id and ent.node_id not in seen_ids:
            seen_ids.add(ent.node_id)
            detailed = await client.icij_get_node(ent.node_id)
            enriched.append(detailed if detailed else ent)

    payload = OffshoreConnectionResult(
        entity_name=entity_name,
        entities=enriched,
    )

    confidence = Confidence.HIGH if len(enriched) >= 3 else (Confidence.MEDIUM if enriched else Confidence.LOW)
    sources = _sources_used(False, False, True)

    return ToolResponse(
        data=payload.model_dump(mode="json"),
        confidence=confidence,
        sources=sources,
        errors=errors,
    ).model_dump(mode="json")


# ---------------------------------------------------------------------------
# Tool: resolve_entity
# ---------------------------------------------------------------------------

@mcp.tool()
async def resolve_entity(name: str, jurisdiction: str = "") -> dict:
    """Resolve an entity across OpenCorporates, GLEIF, and ICIJ Offshore Leaks.

    Attempts to match the entity across all sources and returns consolidated records.
    Optionally filter by jurisdiction (ISO 3166-1 alpha-2 code).
    """
    errors: list[str] = []

    # Search all sources in parallel
    oc_task = client.oc_search_companies(name, jurisdiction=jurisdiction)
    gleif_task = client.gleif_search_lei(name)
    icij_task = client.icij_search(name)

    oc_results, gleif_results, icij_results = await asyncio.gather(
        oc_task, gleif_task, icij_task, return_exceptions=True,
    )

    if isinstance(oc_results, BaseException):
        errors.append(f"OpenCorporates error: {oc_results}")
        oc_results = []
    if isinstance(gleif_results, BaseException):
        errors.append(f"GLEIF error: {gleif_results}")
        gleif_results = []
    if isinstance(icij_results, BaseException):
        errors.append(f"ICIJ error: {icij_results}")
        icij_results = []

    # Filter GLEIF results by jurisdiction if specified
    if jurisdiction and gleif_results:
        jurisdiction_upper = jurisdiction.upper()
        filtered = [r for r in gleif_results if r.country and r.country.upper() == jurisdiction_upper]
        if filtered:
            gleif_results = filtered

    # Filter ICIJ results by jurisdiction if specified
    if jurisdiction and icij_results:
        jurisdiction_lower = jurisdiction.lower()
        filtered_icij = [
            e for e in icij_results
            if e.jurisdiction and jurisdiction_lower in e.jurisdiction.lower()
        ]
        if filtered_icij:
            icij_results = filtered_icij

    payload = ResolvedEntity(
        name=name,
        jurisdiction=jurisdiction or None,
        company_records=oc_results,
        lei_records=gleif_results,
        offshore_entities=icij_results,
    )

    confidence = _assess_confidence(len(oc_results), len(gleif_results), len(icij_results))
    sources = _sources_used(bool(oc_results), bool(gleif_results), bool(icij_results))

    return ToolResponse(
        data=payload.model_dump(mode="json"),
        confidence=confidence,
        sources=sources,
        errors=errors,
    ).model_dump(mode="json")


# ---------------------------------------------------------------------------
# Standalone entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    mcp.run()
