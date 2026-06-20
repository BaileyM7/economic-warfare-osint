"""Person endpoints — insider-threat profiling, candidate search, and co-officer networks.

Extracted from src/api.py (Phase 2 Stage 3). Aggregates OpenSanctions · OFAC SDN ·
OpenCorporates · ICIJ · GDELT · Wikidata PEP · SEC EDGAR for a named individual,
provides lightweight candidate disambiguation, and builds co-officer networks.
Mounted at /api with require_auth applied at include time (see src/api.py).
Behaviour is unchanged from the original inline handlers.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import date

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from src.common.graph_helpers import node as _node
from src.llm import generate_narrative as _generate_narrative
from src.llm import generate_recommendations as _generate_recommendations
from src.orchestrator.person_search import (
    build_person_network,
    build_risk_factors,
    search_persons,
)
from src.tools.corporate.client import icij_search, oc_search_officers
from src.tools.geopolitical.client import gdelt_doc_search
from src.tools.market.client import SECEdgarClient
from src.tools.sanctions.client import SanctionsClient
from src.tools.screening.client import search_csl, search_pep

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["person"])


class PersonProfileRequest(BaseModel):
    name: str
    analyst_question: str = ""


class PersonSearchRequest(BaseModel):
    query: str
    limit: int = 10


class PersonNetworkRequest(BaseModel):
    name: str
    depth: int = 1  # 1 or 2
    max_per_node: int = 15


# --- Person Profile endpoint ---


@router.post("/person-profile")
async def person_profile(req: PersonProfileRequest):
    """Build an insider-threat style profile for a named individual.

    Aggregates: OpenSanctions (person schema) · OFAC SDN · corporate
    affiliations (OpenCorporates officers) · ICIJ offshore connections ·
    GDELT recent news events.
    """
    name = req.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="Name cannot be empty")

    try:
        # Run all lookups concurrently. The sanctions screen now spans CSL
        # (OFAC + BIS + EU + UN aggregated), OFAC SDN directly, AND
        # OpenSanctions (which covers UK SDN + EU + many national lists that
        # CSL doesn't have). The previous CSL + OFAC-only path missed
        # subjects who are only on UK/EU lists -- e.g. Roman Abramovich,
        # heavily sanctioned by UK + EU but not on OFAC SDN.
        sanctions_client = SanctionsClient()
        ofac_client = sanctions_client.ofac
        opensanctions_client = sanctions_client.opensanctions

        sec_client = SECEdgarClient()
        csl_task = asyncio.create_task(search_csl(name))
        ofac_task = asyncio.create_task(ofac_client.search(name, entity_type="person"))
        opensanctions_task = asyncio.create_task(
            opensanctions_client.search_entities(name, entity_type="person")
        )
        officers_task = asyncio.create_task(oc_search_officers(name))
        icij_task = asyncio.create_task(icij_search(name, entity_type="officer"))
        gdelt_task = asyncio.create_task(gdelt_doc_search(name, days=30))
        pep_task = asyncio.create_task(search_pep(name))
        edgar_task = asyncio.create_task(sec_client.get_insider_filings(name))

        (
            csl_hits_raw,
            ofac_hits,
            opensanctions_hits,
            officer_records,
            icij_hits,
            gdelt_events,
            pep_hits,
            insider_filings,
        ) = await asyncio.gather(
            csl_task,
            ofac_task,
            opensanctions_task,
            officers_task,
            icij_task,
            gdelt_task,
            pep_task,
            edgar_task,
            return_exceptions=True,
        )

        def _safe(result, default):
            return default if isinstance(result, Exception) else result

        csl_hits_raw = _safe(csl_hits_raw, [])
        ofac_hits = _safe(ofac_hits, [])
        opensanctions_hits = _safe(opensanctions_hits, [])
        officer_records = _safe(officer_records, [])
        icij_hits = _safe(icij_hits, [])
        gdelt_events = _safe(gdelt_events, {})
        pep_hits = _safe(pep_hits, [])
        insider_filings = _safe(insider_filings, [])

        # CSL + OpenSanctions are merged into the "sanctions_hits" bucket --
        # both use scoring tuned for fuzzy international-list matching.
        sanctions_hits = sanctions_client._csl_to_entries(csl_hits_raw or [])
        sanctions_hits.extend(opensanctions_hits or [])

        # Build sanctions summary
        is_sanctioned = bool(
            [e for e in sanctions_hits if (e.score or 0) >= 0.6]
            or [e for e in ofac_hits if (e.score or 0) >= 0.7]
        )
        sanction_programs: list[str] = []
        for e in ofac_hits:
            if (e.score or 0) >= 0.7 and e.programs:
                sanction_programs.extend(e.programs)
        for e in sanctions_hits:
            if (e.score or 0) >= 0.6 and e.programs:
                sanction_programs.extend(e.programs)
        sanction_programs = list(set(sanction_programs))[:5]

        # Best match for bio data
        best_match = next(
            (e for e in sanctions_hits if (e.score or 0) >= 0.6),
            sanctions_hits[0] if sanctions_hits else None,
        )
        aliases = best_match.aliases if best_match else []
        # Nationality/DOB may appear in identifiers or remarks
        nationality = (
            (best_match.identifiers.get("nationality") or best_match.identifiers.get("citizenship"))
            if best_match
            else None
        )
        dob = best_match.identifiers.get("dob") if best_match else None

        # Corporate affiliations — officer_records are Officer objects
        affiliations = []
        for off in (officer_records or [])[:12]:
            is_active = off.end_date is None if hasattr(off, "end_date") else True
            affiliations.append(
                {
                    "company": off.name,
                    "role": off.role,
                    "nationality": off.nationality or "",
                    "active": is_active,
                }
            )

        # ICIJ connections
        offshore = []
        for h in (icij_hits or [])[:5]:
            offshore.append(
                {
                    "entity": h.name,
                    "dataset": h.source_dataset or "",
                    "jurisdiction": h.jurisdiction or "",
                }
            )

        # Recent events from GDELT (list[GdeltEvent])
        recent_events = []
        if isinstance(gdelt_events, list):
            for ev in gdelt_events[:8]:
                recent_events.append(
                    {
                        "title": ev.event_id[:80] if hasattr(ev, "event_id") else str(ev),
                        "date": ev.date.isoformat() if hasattr(ev, "date") and ev.date else "",
                        "source": ev.source_url if hasattr(ev, "source_url") else "",
                        "tone": ev.avg_tone if hasattr(ev, "avg_tone") else None,
                    }
                )

        # Build person-centric vis.js graph
        nodes: dict[str, dict] = {}
        edges: dict[str, dict] = {}

        def p_slug(s: str) -> str:
            return s.lower().replace(" ", "_").replace(",", "")[:60]

        person_id = f"person_{p_slug(name)}"
        nodes[person_id] = _node(person_id, name, "person")

        for e in [e for e in sanctions_hits if (e.score or 0) >= 0.6][:6]:
            eid = f"sanc_{p_slug(e.name)}"
            nodes[eid] = _node(eid, e.name, "sanctions_list")
            key = f"{person_id}→{eid}"
            edges[key] = {
                "from": person_id,
                "to": eid,
                "label": "OFAC/OS match",
                "arrows": "to",
                "dashes": True,
            }

        # OFAC-only rows (OpenSanctions may be empty without API key)
        for e in [e for e in ofac_hits if (e.score or 0) >= 0.7][:6]:
            slug = getattr(e, "id", None) or p_slug(e.name)
            eid = f"ofac_{p_slug(str(slug))}"
            if eid in nodes:
                continue
            nodes[eid] = _node(eid, e.name, "sanctions_list")
            key = f"{person_id}→{eid}"
            edges[key] = {
                "from": person_id,
                "to": eid,
                "label": "OFAC SDN",
                "arrows": "to",
                "dashes": True,
            }

        for aff in affiliations[:8]:
            cid = f"co_{p_slug(aff['company'])}"
            nodes[cid] = _node(cid, aff["company"], "company")
            key = f"{person_id}→{cid}"
            edges[key] = {
                "from": person_id,
                "to": cid,
                "label": aff.get("role", "officer"),
                "arrows": "to",
                "dashes": False,
            }

        for off in offshore[:4]:
            oid = f"offshore_{p_slug(off['entity'])}"
            nodes[oid] = _node(oid, off["entity"], "theme", off.get("jurisdiction"))
            key = f"{person_id}→{oid}"
            edges[key] = {
                "from": person_id,
                "to": oid,
                "label": "offshore",
                "arrows": "to",
                "dashes": True,
            }

        # Start narrative generation concurrently with graph finalization
        compact = {
            "name": name,
            "is_sanctioned": is_sanctioned,
            "sanction_programs": sanction_programs,
            "nationality": nationality,
            "affiliation_count": len(affiliations),
            "affiliations_preview": [
                {"company": a["company"], "role": a["role"]} for a in affiliations[:4]
            ],
            "offshore_connection_count": len(offshore),
            "recent_event_count": len(recent_events),
            "pep_positions": [p for h in pep_hits for p in h.get("positions", [])][:4],
            "insider_filing_count": len(insider_filings),
            "sources_searched": [
                "OpenSanctions",
                "OFAC SDN",
                "OpenCorporates",
                "ICIJ Offshore Leaks",
                "GDELT",
                "Wikidata PEP",
                "SEC EDGAR Form 4",
            ],
        }
        person_prompt = (
            f"You are an economic warfare analyst writing a due diligence summary for {name}. "
            f"Sources searched: OpenSanctions, OFAC SDN, OpenCorporates (corporate affiliations), "
            f"ICIJ Offshore Leaks, GDELT (recent news), Wikidata (political exposure), "
            f"SEC EDGAR Form 4 (insider transactions). Data as of {date.today().isoformat()}.\n"
            f"Findings: {json.dumps(compact)}\n"
            f"Write 3-5 sentences characterizing this individual's risk profile. "
            f"If no derogatory findings were found, state that clearly and note what the "
            f"clean profile means analytically."
        )
        narrative_task = asyncio.create_task(_generate_narrative(person_prompt))
        coa_task = asyncio.create_task(_generate_recommendations(compact, req.analyst_question))

        graph_result = {
            "nodes": list(nodes.values()),
            "edges": list(edges.values()),
        }
        narrative, recommendations = await asyncio.gather(narrative_task, coa_task)

        risk_factors = build_risk_factors(
            {
                "is_sanctioned": is_sanctioned,
                "sanction_programs": sanction_programs,
                "sanctions_hits": sanctions_hits,
                "ofac_hits": ofac_hits,
                "affiliations": affiliations,
                "offshore": offshore,
                "recent_events": recent_events,
                "pep_hits": pep_hits,
            }
        )

        return JSONResponse(
            content={
                "name": name,
                "is_sanctioned": is_sanctioned,
                "sanction_programs": sanction_programs,
                "aliases": aliases[:6],
                "nationality": nationality,
                "dob": str(dob) if dob else None,
                "affiliations": affiliations,
                "offshore_connections": offshore,
                "recent_events": recent_events,
                "graph": graph_result,
                "narrative": narrative,
                "recommendations": recommendations,
                "risk_factors": [rf.model_dump() for rf in risk_factors],
                "insider_filings": insider_filings,
                "pep_hits": pep_hits,
                "sources": [
                    "OpenSanctions",
                    "OFAC SDN",
                    "OpenCorporates",
                    "ICIJ Offshore Leaks",
                    "GDELT",
                    "Wikidata PEP",
                    "SEC EDGAR Form 4",
                ],
            }
        )

    except Exception as e:
        logger.exception("person_profile error for name=%s", name)
        raise HTTPException(status_code=500, detail=str(e))


# --- Person Search (autocomplete) endpoint ---


@router.post("/person/search")
async def person_search(req: PersonSearchRequest):
    """Lightweight candidate-disambiguation search.

    Concurrent fan-out to OpenSanctions (CSL) and OpenCorporates officer
    search; merges, dedupes, and ranks by `rank_candidates`. Returns at most
    `req.limit` candidates. Distinct from `/api/person-profile`, which is the
    heavy multi-source vetting run on a single canonical name.
    """
    query = (req.query or "").strip()
    if len(query) < 2:
        return JSONResponse(content=[])
    try:
        candidates = await search_persons(query, limit=req.limit)
        return JSONResponse(content=[c.model_dump() for c in candidates])
    except Exception as e:
        logger.exception("person_search error for query=%s", query)
        raise HTTPException(status_code=500, detail=str(e))


# --- Person Network endpoint ---


@router.post("/person/network")
async def person_network(req: PersonNetworkRequest):
    """Build a co-officer network around `name`.

    depth=1: name -> companies they sit on -> co-officers of those companies.
    depth=2: walks one more step from each level-1 co-officer.
    Returns vis.js-shaped {nodes, edges} with sanctions overlay on persons.
    """
    name = (req.name or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="Name cannot be empty")
    if req.depth not in (1, 2):
        raise HTTPException(status_code=400, detail="depth must be 1 or 2")
    try:
        result = await build_person_network(
            name=name,
            depth=req.depth,
            max_per_node=req.max_per_node,
        )
        return JSONResponse(content=result.model_dump(by_alias=True))
    except Exception as e:
        logger.exception("person_network error for name=%s depth=%s", name, req.depth)
        raise HTTPException(status_code=500, detail=str(e))
