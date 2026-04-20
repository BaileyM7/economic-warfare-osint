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

WIKIDATA_SPARQL_URL = "https://query.wikidata.org/sparql"
_CACHE_NS_PEP = "wikidata_pep"
_CACHE_TTL_PEP = 86400  # PEP status rarely changes; cache 24 h


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


async def search_pep(name: str, limit: int = 5) -> list[dict[str, Any]]:
    """Check if a named person is a Politically Exposed Person via Wikidata SPARQL.

    Searches Wikidata for the person by name and checks for:
      - P39 (position held): any government or political office
      - P102 (member of political party)

    Returns a list of dicts, each with:
      name, wikidata_id, positions (list[str]), parties (list[str]),
      countries (list[str]), is_current (bool — True if any position lacks an end date)

    No API key required. Results are cached 24 h (PEP status rarely changes).
    """
    cached = get_cached(_CACHE_NS_PEP, action="pep", q=name)
    if cached is not None:
        return cached

    # Escape double-quotes so the name is safe inside the SPARQL string literal.
    safe_name = name.replace("\\", "\\\\").replace('"', '\\"')

    # Single SPARQL call: fuzzy entity search + label resolution for positions/parties.
    # SERVICE wikibase:mwapi performs the same search as the Wikidata search bar.
    # wdt:P31 wd:Q5 filters to humans only, dropping companies/places/films.
    sparql = f"""
SELECT DISTINCT ?person ?personLabel ?positionLabel ?partyLabel ?countryLabel ?endTime WHERE {{
  SERVICE wikibase:mwapi {{
    bd:serviceParam wikibase:api "EntitySearch" ;
                    wikibase:endpoint "www.wikidata.org" ;
                    mwapi:search "{safe_name}" ;
                    mwapi:language "en" .
    ?person wikibase:apiOutputItem mwapi:item .
  }}
  ?person wdt:P31 wd:Q5 .
  OPTIONAL {{
    ?person p:P39 ?positionStmt .
    ?positionStmt ps:P39 ?position .
    OPTIONAL {{ ?positionStmt pq:P582 ?endTime . }}
  }}
  OPTIONAL {{ ?person wdt:P102 ?party . }}
  OPTIONAL {{ ?person wdt:P27 ?country . }}
  SERVICE wikibase:label {{ bd:serviceParam wikibase:language "en" . }}
}}
LIMIT {limit * 6}
"""

    try:
        data = await fetch_json(
            WIKIDATA_SPARQL_URL,
            params={"query": sparql.strip(), "format": "json"},
            headers={
                "User-Agent": "EconWarfareOSINT admin@example.com",
                "Accept": "application/sparql-results+json",
            },
            timeout=15.0,
        )
    except Exception as exc:
        logger.warning("Wikidata PEP search failed for %r: %s", name, exc)
        return []

    # Group SPARQL rows by person QID, collecting labels.
    by_person: dict[str, dict[str, Any]] = {}
    for row in data.get("results", {}).get("bindings", []):
        person_uri = row.get("person", {}).get("value", "")
        if not person_uri:
            continue
        qid = person_uri.rsplit("/", 1)[-1]   # e.g. "Q12345"

        if qid not in by_person:
            by_person[qid] = {
                "wikidata_id": qid,
                "name": row.get("personLabel", {}).get("value", name),
                "positions": [],
                "parties": [],
                "countries": [],
                "is_current": False,
            }

        entry = by_person[qid]

        pos_label = row.get("positionLabel", {}).get("value", "")
        if pos_label and pos_label not in entry["positions"]:
            entry["positions"].append(pos_label)
            # is_current: the position statement has no P582 (end time).
            if not row.get("endTime"):
                entry["is_current"] = True

        party_label = row.get("partyLabel", {}).get("value", "")
        if party_label and party_label not in entry["parties"]:
            entry["parties"].append(party_label)

        country_label = row.get("countryLabel", {}).get("value", "")
        if country_label and country_label not in entry["countries"]:
            entry["countries"].append(country_label)

    # Return only entries that have at least one PEP indicator.
    results = [
        p for p in by_person.values()
        if p["positions"] or p["parties"]
    ][:limit]

    set_cached(results, _CACHE_NS_PEP, ttl=_CACHE_TTL_PEP, action="pep", q=name)
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
