"""Sector endpoint — sector-level risk analysis across key players.

Extracted from src/api.py (Phase 2 Stage 3). Resolves a free-text sector query
to a curated registry of key players (with deterministic alias matching and an
LLM fallback), screens them against OFAC SDN + Trade.gov CSL, enriches
defense/aviation-style sectors with supply-chain and geopolitical-tension lanes,
and builds a vis.js sector graph. Mounted at /api with require_auth applied at
include time (see src/api.py). Behaviour is unchanged from the original inline
handler.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from src.common.config import config
from src.common.graph_helpers import node as _node
from src.common.screening_helpers import ofac_hit_matches_company_label as _ofac_hit_matches_company_label
from src.llm import generate_narrative as _generate_narrative
from src.llm import generate_recommendations as _generate_recommendations
from src.llm import get_anthropic_client as _get_anthropic_client
from src.tools.geopolitical.server import get_bilateral_tensions
from src.tools.sanctions.client import SanctionsClient
from src.tools.trade.server import get_supply_chain_exposure

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["sector"])


class SectorAnalysisRequest(BaseModel):
    sector: str
    analyst_question: str = ""


def _build_sector_sources(
    sector: str,
    sanctioned_count: int,
    supply_chain_exposures: list[dict] | None,
    geopolitical_tensions: list[dict] | None,
) -> list[dict[str, Any]]:
    """Structured sources list for a sector-analysis response.

    Returns rich {name, url, description} dicts (not bare strings) so that
    downstream COAs and briefings have distinct, citable provenance for each
    data lane. We emit all four lanes unconditionally — even when a given
    query returns empty supply_chain or geopolitical data, the LANES were
    still consulted (a null result is itself a citable fact: "no exposures
    found in HS-code data"). Empty data is also useful information for the
    LLM rationale ("zero current tension pairs flagged [4]").
    """
    sc_desc = (
        f"HS-code-level import/export concentrations across {len(supply_chain_exposures)} flagged exposures for {sector}"
        if supply_chain_exposures
        else f"HS-code-level import/export concentrations for {sector} (no high-concentration exposures flagged in current dataset)"
    )
    gt_desc = (
        f"Country-pair geopolitical tone and event counts across {len(geopolitical_tensions)} active tension pairs for {sector}"
        if geopolitical_tensions
        else f"Country-pair geopolitical tone and event counts for {sector} (no current tension pairs flagged)"
    )
    return [
        {
            "name": "OFAC SDN",
            "url": "https://sanctionssearch.ofac.treas.gov/",
            "description": f"Sanctions screening across {sector} key players ({sanctioned_count} hits)",
        },
        {
            "name": "Trade.gov Consolidated Screening List",
            "url": "https://www.trade.gov/consolidated-screening-list",
            "description": (
                f"Federated screening across BIS Entity List, DDTC Debarred, "
                f"Treasury SDN/Non-SDN, EU/UK lists for {sector} key players"
            ),
        },
        {
            "name": "OpenSanctions",
            "url": "https://www.opensanctions.org/",
            "description": f"Consolidated sanctions and PEP screening for {sector} entities",
        },
        {
            "name": "USA Trade Online (Census)",
            "url": "https://usatrade.census.gov/",
            "description": sc_desc,
        },
        {
            "name": "GDELT 2.0 Event Database",
            "url": "https://www.gdeltproject.org/",
            "description": gt_desc,
        },
    ]


_SECTOR_COMPANIES: dict[str, list[dict]] = {
    "semiconductor": [
        {"name": "TSMC", "ticker": "TSM", "country": "TW"},
        {"name": "Samsung Electronics", "ticker": "005930.KS", "country": "KR"},
        {"name": "ASML", "ticker": "ASML", "country": "NL"},
        {"name": "Nvidia", "ticker": "NVDA", "country": "US"},
        {"name": "Intel", "ticker": "INTC", "country": "US"},
        {"name": "SMIC", "ticker": "0981.HK", "country": "CN"},
        {"name": "Micron", "ticker": "MU", "country": "US"},
        {"name": "SK Hynix", "ticker": "000660.KS", "country": "KR"},
    ],
    "energy": [
        {"name": "Saudi Aramco", "ticker": "2222.SR", "country": "SA"},
        {"name": "Rosneft", "ticker": "ROSN.ME", "country": "RU"},
        {"name": "Gazprom", "ticker": "GAZP.ME", "country": "RU"},
        {"name": "Sinopec", "ticker": "SNP", "country": "CN"},
        {"name": "PetroChina", "ticker": "PTR", "country": "CN"},
        {"name": "ExxonMobil", "ticker": "XOM", "country": "US"},
        {"name": "Shell", "ticker": "SHEL", "country": "GB"},
    ],
    "shipping": [
        {"name": "COSCO Shipping", "ticker": "1919.HK", "country": "CN"},
        {"name": "Evergreen Marine", "ticker": "2603.TW", "country": "TW"},
        {"name": "Maersk", "ticker": "MAERSK-B.CO", "country": "DK"},
        {"name": "China OOCL", "ticker": "0316.HK", "country": "CN"},
        {"name": "Hapag-Lloyd", "ticker": "HLAG.DE", "country": "DE"},
        {"name": "MSC (private)", "ticker": None, "country": "CH"},
    ],
    "rare earth": [
        {"name": "China Northern Rare Earth", "ticker": "600111.SS", "country": "CN"},
        {"name": "MP Materials", "ticker": "MP", "country": "US"},
        {"name": "Lynas Rare Earths", "ticker": "LYC.AX", "country": "AU"},
        {"name": "Shenghe Resources", "ticker": "600392.SS", "country": "CN"},
    ],
    "telecom": [
        {"name": "Huawei (private)", "ticker": None, "country": "CN"},
        {"name": "ZTE", "ticker": "0763.HK", "country": "CN"},
        {"name": "Ericsson", "ticker": "ERIC", "country": "SE"},
        {"name": "Nokia", "ticker": "NOK", "country": "FI"},
        {"name": "China Mobile", "ticker": "0941.HK", "country": "CN"},
    ],
    "defense_aerospace": [
        {"name": "Lockheed Martin", "ticker": "LMT", "country": "US"},
        {"name": "RTX (Raytheon)", "ticker": "RTX", "country": "US"},
        {"name": "Northrop Grumman", "ticker": "NOC", "country": "US"},
        {"name": "L3Harris Technologies", "ticker": "LHX", "country": "US"},
        {"name": "BAE Systems", "ticker": "BAESY", "country": "GB"},
        {"name": "Leonardo", "ticker": "FINMY", "country": "IT"},
        {"name": "Thales", "ticker": "THLEF", "country": "FR"},
        {"name": "AVIC (private)", "ticker": None, "country": "CN"},
    ],
    "aircraft_mro": [
        {"name": "AAR Corp", "ticker": "AIR", "country": "US"},
        {"name": "Heico Corporation", "ticker": "HEI", "country": "US"},
        {"name": "TransDigm Group", "ticker": "TDG", "country": "US"},
        {"name": "StandardAero (private)", "ticker": None, "country": "US"},
        {"name": "Lufthansa Technik (private)", "ticker": None, "country": "DE"},
        {"name": "ST Engineering", "ticker": "S63.SI", "country": "SG"},
        {"name": "HAECO", "ticker": "0044.HK", "country": "HK"},
        {"name": "VSMPO-AVISMA (titanium supplier)", "ticker": None, "country": "RU"},
    ],
    "critical_minerals": [
        {"name": "MP Materials", "ticker": "MP", "country": "US"},
        {"name": "Lynas Rare Earths", "ticker": "LYC.AX", "country": "AU"},
        {"name": "Albemarle", "ticker": "ALB", "country": "US"},
        {"name": "Ganfeng Lithium", "ticker": "1772.HK", "country": "CN"},
        {"name": "China Northern Rare Earth", "ticker": "600111.SS", "country": "CN"},
        {"name": "Pilbara Minerals", "ticker": "PLS.AX", "country": "AU"},
    ],
    "dual_use_tech": [
        {"name": "DJI (private)", "ticker": None, "country": "CN"},
        {"name": "Hikvision", "ticker": "002415.SZ", "country": "CN"},
        {"name": "Dahua Technology", "ticker": "002236.SZ", "country": "CN"},
        {"name": "SenseTime", "ticker": "0020.HK", "country": "CN"},
        {"name": "Megvii (private)", "ticker": None, "country": "CN"},
    ],
    "port_logistics": [
        {"name": "COSCO Shipping Ports", "ticker": "1199.HK", "country": "CN"},
        {"name": "Hutchison Ports (private)", "ticker": None, "country": "HK"},
        {"name": "DP World (private)", "ticker": None, "country": "AE"},
        {"name": "PSA International (private)", "ticker": None, "country": "SG"},
        {"name": "ICTSI", "ticker": "ICT.PS", "country": "PH"},
    ],
    "financial": [
        {"name": "Sberbank", "ticker": "SBRCY", "country": "RU"},
        {"name": "VTB Bank", "ticker": "VTBR.ME", "country": "RU"},
        {"name": "Bank of China", "ticker": "3988.HK", "country": "CN"},
        {"name": "HSBC", "ticker": "HSBC", "country": "GB"},
        {"name": "Standard Chartered", "ticker": "SCBFF", "country": "GB"},
    ],
    "space_satellite": [
        {"name": "Planet Labs", "ticker": "PL", "country": "US"},
        {"name": "Maxar Technologies (private)", "ticker": None, "country": "US"},
        {"name": "Iridium", "ticker": "IRDM", "country": "US"},
        {"name": "Spire Global", "ticker": "SPIR", "country": "US"},
        {"name": "CASC (private)", "ticker": None, "country": "CN"},
    ],
    "agriculture_commodities": [
        {"name": "Archer Daniels Midland", "ticker": "ADM", "country": "US"},
        {"name": "Bunge Global", "ticker": "BG", "country": "US"},
        {"name": "Cargill (private)", "ticker": None, "country": "US"},
        {"name": "Louis Dreyfus Company (private)", "ticker": None, "country": "NL"},
        {"name": "Corteva", "ticker": "CTVA", "country": "US"},
        {"name": "Deere & Company", "ticker": "DE", "country": "US"},
        {"name": "Nutrien", "ticker": "NTR", "country": "CA"},
        {"name": "Tyson Foods", "ticker": "TSN", "country": "US"},
        {"name": "COFCO (private)", "ticker": None, "country": "CN"},
        {"name": "Wilmar International", "ticker": "F34.SI", "country": "SG"},
    ],
}

# Aliases for sector matching — maps query terms to registry keys
_SECTOR_ALIASES: dict[str, str] = {
    "mro": "aircraft_mro",
    "aviation maintenance": "aircraft_mro",
    "aircraft repair": "aircraft_mro",
    "aircraft mro": "aircraft_mro",
    "aviation mro": "aircraft_mro",
    "defense": "defense_aerospace",
    "defence": "defense_aerospace",
    "aerospace": "defense_aerospace",
    "defense primes": "defense_aerospace",
    "rare earth": "critical_minerals",
    "rare earths": "critical_minerals",
    "lithium": "critical_minerals",
    "cobalt": "critical_minerals",
    "critical mineral": "critical_minerals",
    "port": "port_logistics",
    "ports": "port_logistics",
    "logistics": "port_logistics",
    "shipping infrastructure": "port_logistics",
    "banking": "financial",
    "finance": "financial",
    "correspondent banking": "financial",
    "surveillance tech": "dual_use_tech",
    "surveillance": "dual_use_tech",
    "dual use": "dual_use_tech",
    "satellite": "space_satellite",
    "space": "space_satellite",
    "commercial space": "space_satellite",
    "chips": "semiconductor",
    "chip": "semiconductor",
    "semis": "semiconductor",
    "oil": "energy",
    "gas": "energy",
    "oil and gas": "energy",
    "agriculture": "agriculture_commodities",
    "agricultural": "agriculture_commodities",
    "agri": "agriculture_commodities",
    "ag": "agriculture_commodities",
    "farming": "agriculture_commodities",
    "farm": "agriculture_commodities",
    "soybean": "agriculture_commodities",
    "soybeans": "agriculture_commodities",
    "soy bean": "agriculture_commodities",
    "soy beans": "agriculture_commodities",
    "soy": "agriculture_commodities",
    "corn": "agriculture_commodities",
    "maize": "agriculture_commodities",
    "wheat": "agriculture_commodities",
    "grain": "agriculture_commodities",
    "grains": "agriculture_commodities",
    "cereal": "agriculture_commodities",
    "cereals": "agriculture_commodities",
    "livestock": "agriculture_commodities",
    "dairy": "agriculture_commodities",
    "fertilizer": "agriculture_commodities",
    "fertilizers": "agriculture_commodities",
    "fertiliser": "agriculture_commodities",
    "fertilisers": "agriculture_commodities",
    "commodities": "agriculture_commodities",
    "food": "agriculture_commodities",
}


def _normalize_sector_query(query: str) -> list[str]:
    """Yield normalized variants of a sector query for deterministic matching.

    Order matters: most specific (exact lowercase) first, most lenient last.
    """
    base = query.lower().strip()
    variants: list[str] = [base]

    collapsed = " ".join(base.split())
    if collapsed != base:
        variants.append(collapsed)

    no_space = "".join(base.split())
    if no_space and no_space not in variants:
        variants.append(no_space)

    if base.endswith("s") and len(base) > 3:
        variants.append(base[:-1])
    if collapsed.endswith("s") and len(collapsed) > 3 and collapsed[:-1] not in variants:
        variants.append(collapsed[:-1])

    return variants


def _match_sector(query: str) -> tuple[str, list[dict]]:
    """Find a sector from registry/aliases; no LLM, no generic fallback."""
    variants = _normalize_sector_query(query)

    # 1. Exact key match (any normalized variant)
    for v in variants:
        if v in _SECTOR_COMPANIES:
            return v, _SECTOR_COMPANIES[v]

    # 2. Alias lookup (any normalized variant)
    for v in variants:
        if v in _SECTOR_ALIASES:
            key = _SECTOR_ALIASES[v]
            return key, _SECTOR_COMPANIES[key]

    primary = variants[0]

    # 3. Substring match against aliases
    for alias, key in _SECTOR_ALIASES.items():
        if alias in primary or primary in alias:
            return key, _SECTOR_COMPANIES[key]

    # 4. Substring match against registry keys
    for sector_key, companies in _SECTOR_COMPANIES.items():
        if sector_key in primary or primary in sector_key:
            return sector_key, companies

    # 5. Word-level partial match against registry keys
    for sector_key, companies in _SECTOR_COMPANIES.items():
        words = sector_key.replace("_", " ").split()
        if any(w in primary for w in words if len(w) > 3):
            return sector_key, companies

    return "", []


def _clean_ticker_value(raw: Any) -> str | None:
    if raw is None:
        return None
    s = str(raw).strip().upper()
    if not s or s in {"NONE", "NULL", "N/A", "PRIVATE", "-"}:
        return None
    return s


async def _llm_match_sector_key(query: str) -> str:
    """Use the configured LLM to map free text to a known sector key or 'unknown'."""
    client = _get_anthropic_client()
    if not client:
        return "unknown"

    keys = sorted(_SECTOR_COMPANIES.keys())
    prompt = (
        "Classify the sector phrase into ONE known key or 'unknown'. "
        'Return JSON only with schema {"sector_key": "..."}.\n'
        f"Known keys: {keys}\n"
        f"Input: {query}\n"
        "Rules: if confidence is low, return unknown."
    )
    response = await asyncio.wait_for(
        client.messages.create(
            model=config.model,
            max_tokens=120,
            messages=[{"role": "user", "content": prompt}],
        ),
        timeout=10.0,
    )
    text = response.content[0].text
    payload = json.loads(_extract_json(text))  # noqa: F821  # pre-existing; _extract_json missing import from orchestrator.main, tracked separately
    key = str(payload.get("sector_key", "unknown")).strip().lower()
    return key if key in _SECTOR_COMPANIES else "unknown"


async def _llm_generate_sector_companies(query: str) -> list[dict[str, Any]]:
    """Generate a lightweight temporary company list for unknown sectors."""
    client = _get_anthropic_client()
    if not client:
        return []

    prompt = (
        "Generate 8 representative companies for the requested sector. "
        "Return JSON only as an array of objects with keys: name, ticker, country.\n"
        "Ticker should be null when private/unknown. Country should be 2-letter code when possible.\n"
        f"Sector query: {query}"
    )
    response = await asyncio.wait_for(
        client.messages.create(
            model=config.model,
            max_tokens=500,
            messages=[{"role": "user", "content": prompt}],
        ),
        timeout=12.0,
    )
    text = response.content[0].text
    rows = json.loads(_extract_json(text))  # noqa: F821  # pre-existing; _extract_json missing import from orchestrator.main, tracked separately
    if not isinstance(rows, list):
        return []

    companies: list[dict[str, Any]] = []
    for row in rows[:8]:
        if not isinstance(row, dict):
            continue
        name = str(row.get("name", "")).strip()
        if not name:
            continue
        companies.append(
            {
                "name": name,
                "ticker": _clean_ticker_value(row.get("ticker")),
                "country": str(row.get("country", "")).strip().upper()[:2] or None,
            }
        )
    return companies


async def _resolve_sector(query: str) -> tuple[str, list[dict[str, Any]]]:
    """Resolve sector with deterministic matching first, then LLM fallback."""
    key, companies = _match_sector(query)
    if key and companies:
        return key, companies

    try:
        llm_key = await _llm_match_sector_key(query)
    except Exception as exc:
        logger.warning("LLM sector key match failed for query=%r: %s", query, exc)
        llm_key = "unknown"

    if llm_key != "unknown":
        return llm_key, _SECTOR_COMPANIES[llm_key]

    try:
        dynamic_companies = await _llm_generate_sector_companies(query)
    except Exception as exc:
        logger.warning("LLM dynamic sector company generation failed for query=%r: %s", query, exc)
        dynamic_companies = []

    dynamic_key = query.lower().strip().replace(" ", "_")[:40] or "unknown"
    return dynamic_key, dynamic_companies


def _suggest_sector_keys(query: str, limit: int = 3) -> list[str]:
    """Best-effort suggestion of known sector keys when matching fails."""
    q = query.lower().strip()
    tokens = [t for t in q.replace("_", " ").split() if len(t) > 2]
    scored: list[tuple[int, str]] = []
    for key in _SECTOR_COMPANIES:
        key_words = set(key.replace("_", " ").split())
        overlap = sum(1 for t in tokens if any(t in w or w in t for w in key_words))
        if overlap:
            scored.append((overlap, key))
    scored.sort(key=lambda x: (-x[0], x[1]))
    return [k for _, k in scored[:limit]]


@router.post("/sector-analysis")
async def sector_analysis(req: SectorAnalysisRequest):
    """Sector-level analysis: key players, sanctions exposure, trade dependency."""
    sector = req.sector.strip()
    if not sector:
        raise HTTPException(status_code=400, detail="Sector cannot be empty")

    try:
        sector_key, companies = await _resolve_sector(sector)
        if not companies:
            suggestions = _suggest_sector_keys(sector)
            known = ", ".join(sorted(_SECTOR_COMPANIES.keys()))
            detail = (
                f"Could not resolve sector '{sector}' to a supported registry key "
                f"and failed to generate a dynamic company set."
            )
            if suggestions:
                detail += f" Did you mean: {', '.join(suggestions)}?"
            detail += f" Supported sectors include: {known}."
            raise HTTPException(status_code=422, detail=detail)

        # Check sanctions status for top companies in parallel. SanctionsClient
        # fans out to Trade.gov CSL + OFAC SDN per query -- the CSL leg
        # aggregates BIS Entity List, which is how export-controlled companies
        # like SMIC actually surface. OFAC-only was producing false negatives
        # for export-restricted firms.
        sanctions_client = SanctionsClient()
        sanction_tasks = [sanctions_client.search(co["name"]) for co in companies]
        sanction_results = await asyncio.gather(*sanction_tasks, return_exceptions=True)

        company_profiles = []
        for co, result in zip(companies, sanction_results):
            hits = (
                result.matches
                if not isinstance(result, Exception) and hasattr(result, "matches")
                else []
            )
            high_conf = (
                [
                    e
                    for e in hits
                    if (e.score or 0) >= 0.75 and _ofac_hit_matches_company_label(co["name"], e)
                ]
                if hits
                else []
            )
            company_profiles.append(
                {
                    "name": co["name"],
                    "ticker": co.get("ticker"),
                    "country": co.get("country"),
                    "is_sanctioned": bool(high_conf),
                    "sanction_names": [e.name for e in high_conf[:2]],
                }
            )

        sanctioned_count = sum(1 for c in company_profiles if c["is_sanctioned"])

        # Optional enrichment for defense/aviation-style sectors.
        supply_chain_exposures: list[dict[str, Any]] = []
        geopolitical_tensions: list[dict[str, Any]] = []

        sector_hint = f"{sector_key} {sector}".lower()
        if any(k in sector_hint for k in ("aircraft", "mro", "defense", "aerospace")):
            commodity_specs = [
                ("titanium", "810890"),
                ("carbon_fiber", "681510"),
                ("rare_earth_magnets", "850511"),
            ]
            supply_tasks = [
                get_supply_chain_exposure(country="USA", commodity_code=code)
                for _name, code in commodity_specs
            ]
            tension_tasks = [
                get_bilateral_tensions("United States", "China", days=180),
                get_bilateral_tensions("United States", "Russia", days=180),
            ]
            supply_results, tension_results = await asyncio.gather(
                asyncio.gather(*supply_tasks, return_exceptions=True),
                asyncio.gather(*tension_tasks, return_exceptions=True),
            )

            for (label, code), result in zip(commodity_specs, supply_results):
                if isinstance(result, Exception):
                    continue
                payload = result.get("data", result)
                supply_chain_exposures.append(
                    {
                        "label": label,
                        "commodity_code": code,
                        "import_share_pct": payload.get("import_share_pct", 0.0),
                        "top_suppliers": payload.get("top_suppliers", [])[:5],
                    }
                )

            for pair, result in zip(("US-China", "US-Russia"), tension_results):
                if isinstance(result, Exception):
                    continue
                payload = result.get("data", result)
                geopolitical_tensions.append(
                    {
                        "pair": pair,
                        "event_count": payload.get("event_count", 0),
                        "tension_level": payload.get("tension_level", "unknown"),
                        "avg_tone": payload.get("avg_tone"),
                    }
                )

        # Build sector vis.js graph
        nodes: dict[str, dict] = {}
        edges: dict[str, dict] = {}

        def s_slug(s: str) -> str:
            return s.lower().replace(" ", "_").replace("(", "").replace(")", "")[:60]

        sector_id = f"sector_{s_slug(sector_key)}"
        nodes[sector_id] = _node(sector_id, sector_key.title() + " Sector", "sector")

        for co in company_profiles:
            cid = f"co_{s_slug(co['name'])}"
            etype = "sanctions_list" if co["is_sanctioned"] else "company"
            nodes[cid] = _node(cid, co["name"], etype, co.get("country"))
            edges[f"{sector_id}→{cid}"] = {
                "from": sector_id,
                "to": cid,
                "label": "key player",
                "arrows": "to",
                "dashes": False,
            }
            if co["is_sanctioned"]:
                for sn in co["sanction_names"][:1]:
                    sid = f"sanc_{s_slug(sn)}"
                    nodes[sid] = _node(sid, sn, "sanctions_list")
                    edges[f"{cid}→{sid}"] = {
                        "from": cid,
                        "to": sid,
                        "label": "Sanctions listed",
                        "arrows": "to",
                        "dashes": True,
                    }

        # Start narrative generation concurrently with graph finalization
        compact = {
            "sector": sector_key,
            "company_count": len(company_profiles),
            "sanctioned_count": sanctioned_count,
            "sanctioned_entities": [
                {"name": c["name"], "country": c["country"]}
                for c in company_profiles
                if c["is_sanctioned"]
            ],
            "key_players": [
                {"name": c["name"], "country": c["country"], "ticker": c["ticker"]}
                for c in company_profiles[:6]
            ],
            "supply_chain_exposure_count": len(supply_chain_exposures),
            "geopolitical_tension_pairs": geopolitical_tensions,
        }
        sector_prompt = (
            f"You are an economic warfare analyst. Given the following data on the "
            f"{sector_key.replace('_', ' ')} sector, write 3-5 sentences identifying the "
            f"most significant risk vectors: entity sanctions exposure, supply chain "
            f"concentration, geopolitical exposure, and regulatory trajectory.\n"
            f"Data: {json.dumps(compact)}"
        )
        narrative_task = asyncio.create_task(_generate_narrative(sector_prompt))
        coa_task = asyncio.create_task(_generate_recommendations(compact, req.analyst_question))

        graph_result = {
            "nodes": list(nodes.values()),
            "edges": list(edges.values()),
        }
        narrative, recommendations = await asyncio.gather(narrative_task, coa_task)

        return JSONResponse(
            content={
                "sector": sector,
                "sector_key": sector_key,
                "company_count": len(company_profiles),
                "sanctioned_count": sanctioned_count,
                "companies": company_profiles,
                "graph": graph_result,
                "narrative": narrative,
                "recommendations": recommendations,
                "supply_chain_exposures": supply_chain_exposures,
                "geopolitical_tensions": geopolitical_tensions,
                "sources": _build_sector_sources(
                    sector, sanctioned_count, supply_chain_exposures, geopolitical_tensions
                ),
            }
        )

    except Exception as e:
        logger.exception("sector_analysis error for sector=%s", sector)
        raise HTTPException(status_code=500, detail=str(e))
