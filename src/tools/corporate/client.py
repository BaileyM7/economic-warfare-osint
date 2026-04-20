"""Async API clients for OpenCorporates, GLEIF, and ICIJ Offshore Leaks."""

from __future__ import annotations

import asyncio
import logging
from datetime import date
from typing import Any

from ...common.cache import get_cached, set_cached
from ...common.config import config
from ...common.http_client import fetch_json
from .models import (
    CompanyRecord,
    LEIRecord,
    Officer,
    OffshoreEntity,
    OwnershipLink,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# OpenCorporates client
# ---------------------------------------------------------------------------

_OC_BASE = "https://api.opencorporates.com/v0.4"


def _oc_params() -> dict[str, str]:
    """Return auth params if an API key is configured."""
    if config.opencorporates_api_key:
        return {"api_token": config.opencorporates_api_key}
    return {}


def _parse_date(val: str | None) -> date | None:
    if not val:
        return None
    try:
        return date.fromisoformat(val[:10])
    except (ValueError, TypeError):
        return None


def _parse_company(raw: dict[str, Any]) -> CompanyRecord:
    """Parse an OpenCorporates company object into a CompanyRecord."""
    c = raw.get("company", raw)
    industry_codes: list[dict[str, str]] = []
    for ic in c.get("industry_codes", []) or []:
        code_obj = ic.get("industry_code", ic)
        industry_codes.append({
            "code": str(code_obj.get("code", "")),
            "scheme": str(code_obj.get("code_scheme_name", code_obj.get("code_scheme_id", ""))),
        })
    return CompanyRecord(
        name=c.get("name", ""),
        jurisdiction=c.get("jurisdiction_code", ""),
        company_number=c.get("company_number", ""),
        status=c.get("current_status"),
        incorporation_date=_parse_date(c.get("incorporation_date")),
        registered_address=c.get("registered_address_in_full"),
        officers=[],
        industry_codes=industry_codes,
        opencorporates_url=c.get("opencorporates_url"),
    )


def _parse_officer(raw: dict[str, Any]) -> Officer:
    o = raw.get("officer", raw)
    company = o.get("company") or {}
    return Officer(
        name=o.get("name", ""),
        role=o.get("position", o.get("role", "")),
        start_date=_parse_date(o.get("start_date")),
        end_date=_parse_date(o.get("end_date")),
        nationality=o.get("nationality"),
        company_name=company.get("name") or None,
        company_jurisdiction=company.get("jurisdiction_code") or None,
        company_number=company.get("company_number") or None,
        company_url=company.get("opencorporates_url") or None,
    )


async def oc_search_companies(query: str, jurisdiction: str = "") -> list[CompanyRecord]:
    """Search OpenCorporates for companies matching *query*."""
    cache_ns = "oc_search_companies"
    cached = get_cached(cache_ns, query=query, jurisdiction=jurisdiction)
    if cached is not None:
        return [CompanyRecord(**c) for c in cached]

    params: dict[str, Any] = {"q": query, **_oc_params()}
    if jurisdiction:
        params["jurisdiction_code"] = jurisdiction

    try:
        data = await fetch_json(f"{_OC_BASE}/companies/search", params=params)
    except Exception as exc:
        logger.warning("OpenCorporates search failed: %s", exc)
        return []

    companies_raw = (
        data.get("results", {}).get("companies", [])
        if isinstance(data.get("results"), dict)
        else []
    )
    results = [_parse_company(cr) for cr in companies_raw]
    set_cached([r.model_dump(mode="json") for r in results], cache_ns, query=query, jurisdiction=jurisdiction)
    return results


async def oc_get_company(jurisdiction_code: str, company_number: str) -> CompanyRecord | None:
    """Fetch a specific company from OpenCorporates."""
    cache_ns = "oc_get_company"
    cached = get_cached(cache_ns, jurisdiction_code=jurisdiction_code, company_number=company_number)
    if cached is not None:
        return CompanyRecord(**cached)

    try:
        data = await fetch_json(
            f"{_OC_BASE}/companies/{jurisdiction_code}/{company_number}",
            params=_oc_params(),
        )
    except Exception as exc:
        logger.warning("OpenCorporates get company failed: %s", exc)
        return None

    company = _parse_company(data.get("results", {}))

    # Fetch officers for this company
    officers = await oc_search_officers_for_company(jurisdiction_code, company_number)
    company.officers = officers

    set_cached(company.model_dump(mode="json"), cache_ns, jurisdiction_code=jurisdiction_code, company_number=company_number)
    return company


async def oc_search_officers_for_company(jurisdiction_code: str, company_number: str) -> list[Officer]:
    """Fetch officers for a specific company from OpenCorporates."""
    cache_ns = "oc_company_officers"
    cached = get_cached(cache_ns, jurisdiction_code=jurisdiction_code, company_number=company_number)
    if cached is not None:
        return [Officer(**o) for o in cached]

    try:
        data = await fetch_json(
            f"{_OC_BASE}/companies/{jurisdiction_code}/{company_number}/officers",
            params=_oc_params(),
        )
    except Exception as exc:
        logger.warning("OpenCorporates officers fetch failed: %s", exc)
        return []

    officers_raw = (
        data.get("results", {}).get("officers", [])
        if isinstance(data.get("results"), dict)
        else []
    )
    results = [_parse_officer(o) for o in officers_raw]
    set_cached([r.model_dump(mode="json") for r in results], cache_ns, jurisdiction_code=jurisdiction_code, company_number=company_number)
    return results


async def oc_search_officers(name: str) -> list[Officer]:
    """Search OpenCorporates for officers by name."""
    cache_ns = "oc_search_officers"
    cached = get_cached(cache_ns, name=name)
    if cached is not None:
        return [Officer(**o) for o in cached]

    try:
        data = await fetch_json(
            f"{_OC_BASE}/officers/search",
            params={"q": name, **_oc_params()},
        )
    except Exception as exc:
        logger.warning("OpenCorporates officer search failed: %s", exc)
        return []

    officers_raw = (
        data.get("results", {}).get("officers", [])
        if isinstance(data.get("results"), dict)
        else []
    )
    results = [_parse_officer(o) for o in officers_raw]
    set_cached([r.model_dump(mode="json") for r in results], cache_ns, name=name)
    return results


# ---------------------------------------------------------------------------
# GLEIF client
# ---------------------------------------------------------------------------

_GLEIF_BASE = "https://api.gleif.org/api/v1"
_GLEIF_HEADERS = {"Accept": "application/vnd.api+json"}


def _parse_lei_record(raw: dict[str, Any]) -> LEIRecord:
    """Parse a GLEIF lei-record JSON:API resource into an LEIRecord."""
    attrs = raw.get("attributes", {})
    entity = attrs.get("entity", {})
    legal_address = entity.get("legalAddress", {})
    reg = attrs.get("registration", {})

    return LEIRecord(
        lei=raw.get("id", attrs.get("lei", "")),
        legal_name=entity.get("legalName", {}).get("name", ""),
        country=legal_address.get("country"),
        jurisdiction=entity.get("jurisdiction"),
        status=entity.get("status") or reg.get("status"),
        registration_date=_parse_date(reg.get("initialRegistrationDate")),
        last_update=_parse_date(reg.get("lastUpdateDate")),
    )


async def gleif_search_lei(query: str) -> list[LEIRecord]:
    """Full-text search for LEI records in GLEIF."""
    cache_ns = "gleif_search"
    cached = get_cached(cache_ns, query=query)
    if cached is not None:
        return [LEIRecord(**r) for r in cached]

    try:
        data = await fetch_json(
            f"{_GLEIF_BASE}/lei-records",
            params={"filter[fulltext]": query, "page[size]": "20"},
            headers=_GLEIF_HEADERS,
        )
    except Exception as exc:
        logger.warning("GLEIF search failed: %s", exc)
        return []

    records = [_parse_lei_record(r) for r in data.get("data", [])]
    set_cached([r.model_dump(mode="json") for r in records], cache_ns, query=query)
    return records


async def gleif_get_direct_parent(lei: str) -> OwnershipLink | None:
    """Get the direct parent relationship for an LEI."""
    cache_ns = "gleif_direct_parent"
    cached = get_cached(cache_ns, lei=lei)
    if cached is not None:
        return OwnershipLink(**cached) if cached else None

    try:
        data = await fetch_json(
            f"{_GLEIF_BASE}/lei-records/{lei}/direct-parent-relationship",
            headers=_GLEIF_HEADERS,
        )
    except Exception as exc:
        logger.debug("GLEIF direct parent not found for %s", lei)
        set_cached({}, cache_ns, lei=lei)
        return None

    rel_data = data.get("data")
    if not rel_data:
        set_cached({}, cache_ns, lei=lei)
        return None

    relationships = rel_data if isinstance(rel_data, list) else [rel_data]
    if not relationships:
        set_cached({}, cache_ns, lei=lei)
        return None

    rel = relationships[0]
    rel_attrs = rel.get("attributes", {})
    parent_id = ""
    # The parent LEI is in the relationship links
    rel_links = rel.get("relationships", {})
    parent_record = rel_links.get("parent-lei-record", {}).get("data", {})
    if isinstance(parent_record, dict):
        parent_id = parent_record.get("id", "")

    link = OwnershipLink(
        parent_id=parent_id,
        child_id=lei,
        ownership_pct=None,
        relationship_type="direct_parent",
    )
    set_cached(link.model_dump(mode="json"), cache_ns, lei=lei)
    return link


async def gleif_get_ultimate_parent(lei: str) -> OwnershipLink | None:
    """Get the ultimate parent relationship for an LEI."""
    cache_ns = "gleif_ultimate_parent"
    cached = get_cached(cache_ns, lei=lei)
    if cached is not None:
        return OwnershipLink(**cached) if cached else None

    try:
        data = await fetch_json(
            f"{_GLEIF_BASE}/lei-records/{lei}/ultimate-parent-relationship",
            headers=_GLEIF_HEADERS,
        )
    except Exception as exc:
        logger.debug("GLEIF ultimate parent not found for %s", lei)
        set_cached({}, cache_ns, lei=lei)
        return None

    rel_data = data.get("data")
    if not rel_data:
        set_cached({}, cache_ns, lei=lei)
        return None

    relationships = rel_data if isinstance(rel_data, list) else [rel_data]
    if not relationships:
        set_cached({}, cache_ns, lei=lei)
        return None

    rel = relationships[0]
    rel_links = rel.get("relationships", {})
    parent_record = rel_links.get("parent-lei-record", {}).get("data", {})
    parent_id = ""
    if isinstance(parent_record, dict):
        parent_id = parent_record.get("id", "")

    link = OwnershipLink(
        parent_id=parent_id,
        child_id=lei,
        ownership_pct=None,
        relationship_type="ultimate_parent",
    )
    set_cached(link.model_dump(mode="json"), cache_ns, lei=lei)
    return link


async def gleif_get_lei_record(lei: str) -> LEIRecord | None:
    """Fetch a single LEI record by its LEI code."""
    cache_ns = "gleif_get_lei"
    cached = get_cached(cache_ns, lei=lei)
    if cached is not None:
        return LEIRecord(**cached) if cached else None

    try:
        data = await fetch_json(
            f"{_GLEIF_BASE}/lei-records/{lei}",
            headers=_GLEIF_HEADERS,
        )
    except Exception as exc:
        logger.warning("GLEIF get LEI failed for %s: %s", lei, exc)
        return None

    record_data = data.get("data")
    if not record_data:
        return None

    record = _parse_lei_record(record_data)
    set_cached(record.model_dump(mode="json"), cache_ns, lei=lei)
    return record


# ---------------------------------------------------------------------------
# ICIJ Offshore Leaks client
# ---------------------------------------------------------------------------

_ICIJ_BASE = "https://offshoreleaks.icij.org/api/v1"


def _parse_offshore_entity(raw: dict[str, Any]) -> OffshoreEntity:
    """Parse an ICIJ node/search result into an OffshoreEntity."""
    return OffshoreEntity(
        node_id=str(raw.get("node_id", raw.get("id", ""))),
        name=raw.get("name", raw.get("entity", "")),
        jurisdiction=raw.get("jurisdiction", raw.get("jurisdiction_description")),
        source_dataset=raw.get("sourceID", raw.get("source_id", raw.get("source_dataset"))),
        address=raw.get("address"),
        linked_to=[n for n in (raw.get("linked_to") or []) if isinstance(n, str)],
        note=raw.get("note"),
    )


async def icij_search(query: str, entity_type: str = "entity") -> list[OffshoreEntity]:
    """Search the ICIJ Offshore Leaks database."""
    cache_ns = "icij_search"
    cached = get_cached(cache_ns, query=query, type=entity_type)
    if cached is not None:
        return [OffshoreEntity(**e) for e in cached]

    try:
        data = await fetch_json(
            f"{_ICIJ_BASE}/search",
            params={"q": query, "type": entity_type},
        )
    except Exception as exc:
        logger.warning("ICIJ search failed: %s", exc)
        return []

    # The API response structure may vary; handle both list and dict shapes
    if isinstance(data, list):
        results_raw = data
    elif isinstance(data, dict):
        results_raw = data.get("data", data.get("results", data.get("nodes", [])))
        if isinstance(results_raw, dict):
            results_raw = results_raw.get("results", [])
    else:
        results_raw = []

    results = [_parse_offshore_entity(r) for r in results_raw if isinstance(r, dict)]
    set_cached([r.model_dump(mode="json") for r in results], cache_ns, query=query, type=entity_type)
    return results


async def icij_get_node(node_id: str) -> OffshoreEntity | None:
    """Fetch details for a specific ICIJ node."""
    cache_ns = "icij_node"
    cached = get_cached(cache_ns, node_id=node_id)
    if cached is not None:
        return OffshoreEntity(**cached) if cached else None

    try:
        data = await fetch_json(f"{_ICIJ_BASE}/nodes/{node_id}")
    except Exception as exc:
        logger.warning("ICIJ node lookup failed for %s: %s", node_id, exc)
        return None

    if not data:
        return None

    node_data = data.get("data", data) if isinstance(data, dict) else data
    if not isinstance(node_data, dict):
        return None

    entity = _parse_offshore_entity(node_data)
    set_cached(entity.model_dump(mode="json"), cache_ns, node_id=node_id)
    return entity
