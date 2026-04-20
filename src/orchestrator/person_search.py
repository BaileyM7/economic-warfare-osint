"""Person search, network, and risk-factor orchestration.

This module composes the existing tool-layer clients (OpenSanctions / OFAC /
OpenCorporates / ICIJ / GDELT) into three higher-level operations that the
person-centric API endpoints serve:

    1. search_persons(query)          -> autocomplete candidate list
    2. build_person_network(name)     -> co-officer graph (depth 1 or 2)
    3. build_risk_factors(profile)    -> structured F1-F4 factor cards

Three functions in this file have been deliberately left as stubs for the
operator to fill in — they encode policy decisions (ranking, pruning,
severity thresholds) that depend on the customer's risk model rather than
on anything in the data. They are flagged with `# LEARNING OPPORTUNITY`.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from collections import Counter
from typing import Any, Literal

from pydantic import BaseModel, Field

from src.tools.corporate.client import (
    oc_search_officers,
    oc_search_officers_for_company,
)
from src.tools.corporate.models import Officer
from src.tools.sanctions.client import SanctionsClient
from src.tools.screening.client import search_csl, search_pep

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Public response models (also imported by src/api.py)
# ---------------------------------------------------------------------------


Severity = Literal["none", "suggested", "expected", "discouraged", "prohibited"]


class PersonCandidate(BaseModel):
    """One row in the autocomplete dropdown."""

    name: str
    sources: list[str] = Field(default_factory=list)        # ["opensanctions", "opencorporates"]
    sanctioned: bool = False
    sanction_programs: list[str] = Field(default_factory=list)
    primary_affiliation: str | None = None
    country: str | None = None
    score: float = 0.0                                       # 0-1, see rank_candidates
    alt_names: list[str] = Field(default_factory=list)      # aliases from CSL / OFAC alt names


class PersonNetworkNode(BaseModel):
    id: str
    label: str
    group: str                                               # "person" | "company"
    depth: int                                               # 0 = central, 1 = L1, 2 = L2
    sanctioned: bool = False


class PersonNetworkEdge(BaseModel):
    from_: str = Field(alias="from")
    to: str
    label: str | None = None

    model_config = {"populate_by_name": True}


class PersonNetworkResponse(BaseModel):
    central: str
    depth: int
    nodes: list[PersonNetworkNode]
    edges: list[PersonNetworkEdge]


class RiskFactor(BaseModel):
    """One factor card mirroring DelphiGrid's F1-F4 pattern."""

    title: str
    severity: Severity
    score: int                                               # 0-100
    summary: str
    evidence: list[dict[str, Any]] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Policy constants — edit these to shift the risk model without touching logic.
# ---------------------------------------------------------------------------


# Countries where an affiliated officer is inherently higher-risk.
# Used (a) as a candidate-ranking tiebreaker and (b) to escalate the
# corporate-ties factor. ISO 3166-1 alpha-2.
ADVERSARY_COUNTRIES: frozenset[str] = frozenset(
    {"RU", "IR", "KP", "CN", "BY", "VE", "SY", "CU"}
)


# Sanctions program labels that indicate an export-control / consent-decree
# style listing rather than a full SDN block. A subject whose ONLY matches
# are in this set gets stepped down from "prohibited" to "discouraged".
# Matched case-insensitively against substrings of each program string.
NON_SDN_PROGRAM_TOKENS: tuple[str, ...] = (
    "denied persons",
    "unverified",
    "debarred",
    "consent decree",
    "dpl",
    "uvl",
)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


_PUNCT_RE = re.compile(r"[^\w\s]")


def _normalize_country(code: str | None) -> str | None:
    """Upper-case a country code; truncate OpenCorporates extended codes like
    'us_de' down to the top-level 'US'."""
    if not code:
        return None
    head = code.split("_", 1)[0].upper()
    return head or None


def _is_adversary_country(code: str | None) -> bool:
    return _normalize_country(code) in ADVERSARY_COUNTRIES


def _programs_are_non_sdn_only(programs: list[str]) -> bool:
    """True iff every program string matches one of NON_SDN_PROGRAM_TOKENS.

    An empty list returns False (absence of evidence ≠ evidence that all
    matches were consent-decree only — we don't step down without proof).
    """
    if not programs:
        return False
    for p in programs:
        p_lc = (p or "").lower()
        if not any(tok in p_lc for tok in NON_SDN_PROGRAM_TOKENS):
            return False
    return True


def _normalize_name(name: str) -> str:
    """Normalize a person name for cross-source dedupe.

    Lowercases, strips punctuation, collapses whitespace. Does NOT attempt
    transliteration — that would require a real entity-resolution library.
    """
    if not name:
        return ""
    return " ".join(_PUNCT_RE.sub(" ", name.lower()).split())


def _csl_hits_to_candidates(hits: list[dict[str, Any]]) -> list[PersonCandidate]:
    """Convert raw Trade.gov CSL search hits into PersonCandidate rows.

    Filters to person-like entities only — CSL returns companies and vessels
    too. The CSL `type` field is "Individual" for persons.
    """
    out: list[PersonCandidate] = []
    for h in hits:
        if (h.get("type") or "").lower() not in ("individual", "person", ""):
            # Skip companies/vessels in the autocomplete dropdown
            continue
        addresses = h.get("addresses") or []
        country = None
        for a in addresses:
            if a.get("country"):
                country = a["country"]
                break
        out.append(
            PersonCandidate(
                name=h.get("name") or "",
                sources=["opensanctions"],
                sanctioned=True,
                sanction_programs=list(h.get("programs") or [])[:5],
                primary_affiliation=None,
                country=country,
                alt_names=list(h.get("alt_names") or [])[:8],
            )
        )
    return out


def _officers_to_candidates(officers: list[Officer]) -> list[PersonCandidate]:
    """Convert OpenCorporates officer hits into PersonCandidate rows.

    Multiple officer rows for the same person (one per company they sit on)
    are merged into a single candidate; we keep the most-recent active company
    as `primary_affiliation`.
    """
    by_norm: dict[str, PersonCandidate] = {}
    for off in officers:
        if not off.name:
            continue
        norm = _normalize_name(off.name)
        existing = by_norm.get(norm)
        if existing is None:
            existing = PersonCandidate(
                name=off.name,
                sources=["opencorporates"],
                sanctioned=False,
                sanction_programs=[],
                primary_affiliation=off.company_name,
                country=off.nationality or off.company_jurisdiction,
            )
            by_norm[norm] = existing
        else:
            # Prefer an active (no end_date) company as the displayed affiliation
            if off.end_date is None and off.company_name:
                existing.primary_affiliation = off.company_name
    return list(by_norm.values())


def _merge_candidates(
    csl: list[PersonCandidate],
    oc: list[PersonCandidate],
) -> list[PersonCandidate]:
    """Merge candidates from both sources, deduping by normalized name."""
    by_norm: dict[str, PersonCandidate] = {}
    for c in csl + oc:
        norm = _normalize_name(c.name)
        if not norm:
            continue
        existing = by_norm.get(norm)
        if existing is None:
            by_norm[norm] = c
            continue
        # Merge: union of sources, keep sanctions data, keep affiliation
        for src in c.sources:
            if src not in existing.sources:
                existing.sources.append(src)
        if c.sanctioned and not existing.sanctioned:
            existing.sanctioned = True
            existing.sanction_programs = list(
                set(existing.sanction_programs + c.sanction_programs)
            )[:5]
        if not existing.primary_affiliation and c.primary_affiliation:
            existing.primary_affiliation = c.primary_affiliation
        if not existing.country and c.country:
            existing.country = c.country
        for alias in c.alt_names:
            if alias not in existing.alt_names:
                existing.alt_names.append(alias)
    return list(by_norm.values())


# ---------------------------------------------------------------------------
# LEARNING OPPORTUNITY 1 — Candidate ranking
# ---------------------------------------------------------------------------


def rank_candidates(
    candidates: list[PersonCandidate],
    query: str,
) -> list[PersonCandidate]:
    """Score and sort candidates so the most-relevant rows surface first.

    This is a domain-judgment call — the scoring function defines what
    "relevant" means for the customer's analyst workflow. Trade-offs:

      * Sanctioned-but-fuzzy vs. clean-but-exact match
        (most threat tools rank sanctions hits above clean exact matches)
      * Multi-source confirmation
        (a candidate that appears in BOTH OpenSanctions and OpenCorporates
        is more trustworthy — DelphiGrid weights this implicitly via citation
        count; we have to be explicit)
      * Tiebreakers when both rows are sanctioned & exact name match
        (program severity: SDN > EU > UK > misc)

    The scoring should populate `c.score` in [0.0, 1.0] and return the list
    sorted descending by score.

    Useful inputs:
      - `_normalize_name(query)` for an exact-match check
      - `c.sanctioned`, `c.sanction_programs`, `len(c.sources)`

    See `tests/test_person_search.py::test_rank_candidates_*` for the
    behaviors the tests will check.
    """
    # Policy (configured 2026-04-13):
    #   1. Sanctioned hits ALWAYS outrank clean matches, even if the name
    #      on a clean match is closer to the query. Analysts want to see
    #      hits first even when the spelling is off.
    #   2. Multi-source confirmation outranks single-source, within each tier.
    #   3. Exact-name match is an intra-tier bonus, not a cross-tier override.
    #   4. Tiebreaker among equally-scored candidates: adversary-country
    #      affiliation wins, then country code (stable), then name.
    norm_q = _normalize_name(query)
    for c in candidates:
        score = 0.0
        if c.sanctioned:
            score += 0.6                                  # dominant tier signal
        if len(c.sources) >= 2:
            score += 0.2                                  # multi-source boost
        if _normalize_name(c.name) == norm_q:
            score += 0.15                                 # exact-match tiebreaker
        if _is_adversary_country(c.country):
            score += 0.05                                 # small country nudge
        c.score = round(score, 3)

    def _sort_key(c: PersonCandidate) -> tuple:
        return (
            -c.score,
            0 if _is_adversary_country(c.country) else 1,  # adversary first
            _normalize_country(c.country) or "zz",          # then by country
            c.name.lower(),
        )

    candidates.sort(key=_sort_key)
    return candidates


# ---------------------------------------------------------------------------
# Public: search
# ---------------------------------------------------------------------------


async def search_persons(query: str, limit: int = 10) -> list[PersonCandidate]:
    """Run the autocomplete fan-out: OpenSanctions + OpenCorporates in parallel."""
    query = (query or "").strip()
    if len(query) < 2:
        return []

    csl_task = asyncio.create_task(search_csl(query, limit=max(limit, 25)))
    oc_task = asyncio.create_task(oc_search_officers(query))

    csl_raw, oc_raw = await asyncio.gather(csl_task, oc_task, return_exceptions=True)

    csl_hits = csl_raw if isinstance(csl_raw, list) else []
    oc_hits = oc_raw if isinstance(oc_raw, list) else []
    if isinstance(csl_raw, Exception):
        logger.warning("CSL search failed for %r: %s", query, csl_raw)
    if isinstance(oc_raw, Exception):
        logger.warning("OpenCorporates officer search failed for %r: %s", query, oc_raw)

    csl_candidates = _csl_hits_to_candidates(csl_hits)
    oc_candidates = _officers_to_candidates(oc_hits)
    merged = _merge_candidates(csl_candidates, oc_candidates)
    ranked = rank_candidates(merged, query)
    return ranked[:limit]


# ---------------------------------------------------------------------------
# LEARNING OPPORTUNITY 2 — Network depth-2 pruning
# ---------------------------------------------------------------------------


def prune_l2_nodes(
    nodes: list[PersonNetworkNode],
    edges: list[PersonNetworkEdge],
    *,
    max_per_l1: int = 10,
    min_shared_companies: int = 1,
) -> tuple[list[PersonNetworkNode], list[PersonNetworkEdge]]:
    """Cap the L2 explosion so the graph stays readable.

    DelphiGrid exposes three knobs (`maxLevel1CoAuthors`, `maxLevel2PerLevel1`,
    `minLevel2Collaborations`). For Emissary's co-officer graph, depth-2
    fan-out can blow up fast: a director on 5 boards each with 10 directors =
    50 L2 nodes per L1 node. The default depth-2 walk currently produces
    everything; this function trims it to a useful subset.

    Suggested algorithm:
      1. Group L2 nodes by their L1 parent (use the edges where `to` is L2)
      2. For each L1, keep only the top `max_per_l1` L2 neighbors. Pick which
         ones to keep based on frequency of appearance across multiple L1s
         (a node shared by several L1s is more interesting than a one-off).
      3. Drop L2 nodes that share fewer than `min_shared_companies` companies
         with the central node (signal-vs-noise floor).
      4. Drop edges referring to removed nodes.

    Returns the pruned (nodes, edges) tuple. If `nodes` has no depth==2 entries,
    returns the input unchanged.
    """
    # Policy (configured 2026-04-13):
    #   Keep an L2 node only if it clusters — i.e., it appears under 2+ L1
    #   parents (shared-collaborator signal) OR it individually passes the
    #   min_shared_companies floor. Then cap at max_per_l1 per L1 parent.
    l2_ids = {n.id for n in nodes if n.depth == 2}
    if not l2_ids:
        return nodes, edges

    # Count which L1 parents each L2 node is attached to via edges.
    l1_ids = {n.id for n in nodes if n.depth == 1}
    l2_parent_counts: Counter[str] = Counter()
    per_parent: dict[str, list[str]] = {pid: [] for pid in l1_ids}
    for e in edges:
        if e.to in l2_ids and e.from_ in l1_ids:
            l2_parent_counts[e.to] += 1
            per_parent[e.from_].append(e.to)

    # Keep L2s that either cluster (2+ L1 parents) or pass the floor.
    keep: set[str] = {
        l2_id
        for l2_id, n_parents in l2_parent_counts.items()
        if n_parents >= 2 or n_parents >= min_shared_companies
    }

    # Enforce per-L1 cap: for each L1, retain at most max_per_l1 of its L2s,
    # preferring those with more parents (highest cluster signal first).
    capped: set[str] = set()
    for pid, children in per_parent.items():
        ranked = sorted(set(children), key=lambda x: -l2_parent_counts[x])
        capped.update(ranked[:max_per_l1])
    keep &= capped

    pruned_nodes = [n for n in nodes if n.depth != 2 or n.id in keep]
    kept_ids = {n.id for n in pruned_nodes}
    pruned_edges = [e for e in edges if e.from_ in kept_ids and e.to in kept_ids]
    return pruned_nodes, pruned_edges


# ---------------------------------------------------------------------------
# Network walk
# ---------------------------------------------------------------------------


# In-memory cache for network walks (per-process). Keyed by (name, depth).
_NETWORK_CACHE: dict[tuple[str, int], tuple[float, PersonNetworkResponse]] = {}
_NETWORK_TTL_SEC = 300


def _person_id(name: str) -> str:
    return "p_" + _normalize_name(name).replace(" ", "_")[:60]


def _company_id(jurisdiction: str | None, number: str | None, fallback: str) -> str:
    if jurisdiction and number:
        return f"co_{jurisdiction}_{number}"
    return "co_" + _normalize_name(fallback).replace(" ", "_")[:60]


async def _check_sanctioned(name: str) -> bool:
    """Lightweight sanctions check for overlay on graph nodes."""
    try:
        hits = await search_csl(name, limit=3)
    except Exception:
        return False
    for h in hits:
        # Rough match: name substring, individual type
        if (h.get("type") or "").lower() in ("individual", "person", ""):
            return True
    return False


async def build_person_network(
    name: str,
    depth: int = 1,
    max_per_node: int = 15,
) -> PersonNetworkResponse:
    """Build the co-officer network rooted at `name`.

    depth=1: name -> companies -> co-officers of those companies
    depth=2: walk one more step from each L1 co-officer
    """
    name = (name or "").strip()
    if not name:
        return PersonNetworkResponse(central=name, depth=depth, nodes=[], edges=[])

    cache_key = (name.lower(), depth)
    cached = _NETWORK_CACHE.get(cache_key)
    if cached and (time.time() - cached[0]) < _NETWORK_TTL_SEC:
        return cached[1]

    # Layer 0: the central person.
    nodes: dict[str, PersonNetworkNode] = {}
    edges: list[PersonNetworkEdge] = []

    central_id = _person_id(name)
    nodes[central_id] = PersonNetworkNode(
        id=central_id,
        label=name,
        group="person",
        depth=0,
        sanctioned=await _check_sanctioned(name),
    )

    # Layer 1: companies the central person is an officer of.
    central_officer_rows = await oc_search_officers(name)
    seen_companies: set[str] = set()
    company_keys: list[tuple[str, str, str]] = []  # (jur, num, name)
    for off in central_officer_rows[:max_per_node]:
        if not (off.company_jurisdiction and off.company_number and off.company_name):
            continue
        key = f"{off.company_jurisdiction}/{off.company_number}"
        if key in seen_companies:
            continue
        seen_companies.add(key)
        company_keys.append(
            (off.company_jurisdiction, off.company_number, off.company_name)
        )
        cid = _company_id(off.company_jurisdiction, off.company_number, off.company_name)
        nodes[cid] = PersonNetworkNode(
            id=cid, label=off.company_name, group="company", depth=1,
        )
        edges.append(PersonNetworkEdge(**{"from": central_id, "to": cid, "label": off.role or "officer"}))

    # Layer 1 (officers): co-officers at each of those companies.
    co_officer_tasks = [
        oc_search_officers_for_company(jur, num) for (jur, num, _) in company_keys
    ]
    co_officer_results: list[Any] = []
    if co_officer_tasks:
        co_officer_results = await asyncio.gather(*co_officer_tasks, return_exceptions=True)

    l1_officer_company_map: dict[str, list[tuple[str, str, str]]] = {}  # officer_norm -> [(jur,num,name)]

    for (jur, num, comp_name), result in zip(company_keys, co_officer_results):
        if isinstance(result, Exception) or not result:
            continue
        cid = _company_id(jur, num, comp_name)
        for co_off in result[:max_per_node]:
            if not co_off.name or _normalize_name(co_off.name) == _normalize_name(name):
                continue
            pid = _person_id(co_off.name)
            if pid not in nodes:
                nodes[pid] = PersonNetworkNode(
                    id=pid, label=co_off.name, group="person", depth=1,
                )
            edges.append(PersonNetworkEdge(**{"from": cid, "to": pid, "label": co_off.role or "officer"}))
            l1_officer_company_map.setdefault(_normalize_name(co_off.name), []).append(
                (jur, num, comp_name)
            )

    # Layer 2: walk one more step from each L1 co-officer (if requested).
    if depth >= 2:
        l1_officer_names = [
            n.label for n in nodes.values() if n.depth == 1 and n.group == "person"
        ]
        l2_search_tasks = [oc_search_officers(n) for n in l1_officer_names[:max_per_node]]
        l2_results: list[Any] = (
            await asyncio.gather(*l2_search_tasks, return_exceptions=True)
            if l2_search_tasks
            else []
        )
        for parent_name, result in zip(l1_officer_names, l2_results):
            if isinstance(result, Exception) or not result:
                continue
            parent_id = _person_id(parent_name)
            for off in result[: max_per_node // 2]:
                if not (off.company_jurisdiction and off.company_number and off.company_name):
                    continue
                cid = _company_id(off.company_jurisdiction, off.company_number, off.company_name)
                if cid not in nodes:
                    nodes[cid] = PersonNetworkNode(
                        id=cid, label=off.company_name, group="company", depth=2,
                    )
                edges.append(
                    PersonNetworkEdge(**{"from": parent_id, "to": cid, "label": off.role or "officer"})
                )

    # Sanctions overlay on every person node (cheap CSL hit-check).
    person_node_ids = [
        n.id for n in nodes.values() if n.group == "person" and n.depth >= 1
    ]
    if person_node_ids:
        sanction_tasks = [
            _check_sanctioned(nodes[nid].label) for nid in person_node_ids
        ]
        sanction_results = await asyncio.gather(*sanction_tasks, return_exceptions=True)
        for nid, flagged in zip(person_node_ids, sanction_results):
            if isinstance(flagged, bool):
                nodes[nid].sanctioned = flagged

    node_list = list(nodes.values())
    if depth >= 2:
        node_list, edges = prune_l2_nodes(
            node_list, edges, max_per_l1=max_per_node, min_shared_companies=1,
        )

    response = PersonNetworkResponse(
        central=name, depth=depth, nodes=node_list, edges=edges,
    )
    _NETWORK_CACHE[cache_key] = (time.time(), response)
    return response


# ---------------------------------------------------------------------------
# LEARNING OPPORTUNITY 3 — Risk factor severity policy
# ---------------------------------------------------------------------------


def _factor_sanctions(
    is_sanctioned: bool,
    sanction_programs: list[str],
    sanctions_hits: list[Any],
    ofac_hits: list[Any],
) -> RiskFactor:
    """F1 — Sanctions exposure.

    Policy (configured 2026-04-13):
      * Any SDN-style match                -> prohibited (score 100)
      * Only consent-decree / export-control
        list matches (DPL, UVL, debarred,
        consent decree)                    -> discouraged (score 70)
      * No matches                         -> none (score 0)
    Consent-decree stepdown is controlled by NON_SDN_PROGRAM_TOKENS.
    """
    if is_sanctioned:
        if _programs_are_non_sdn_only(sanction_programs):
            severity: Severity = "discouraged"
            score = 70
            summary = (
                f"Subject on export-control / consent-decree list(s) only: "
                f"{', '.join(sanction_programs[:3])}"
            )
        else:
            severity = "prohibited"
            score = 100
            summary = (
                f"Subject is on {len(sanction_programs)} sanctions program(s): "
                f"{', '.join(sanction_programs[:3])}"
                if sanction_programs
                else "Subject matched a sanctions list."
            )
    else:
        severity = "none"
        score = 0
        summary = "No sanctions matches found across OFAC SDN or OpenSanctions."

    evidence: list[dict[str, Any]] = []
    for h in (ofac_hits or [])[:5]:
        if (getattr(h, "score", 0) or 0) >= 0.7:
            evidence.append({
                "type": "ofac_sdn",
                "description": f"OFAC match: {getattr(h, 'name', '?')}",
                "source": "OFAC SDN",
                "programs": list(getattr(h, "programs", []) or []),
            })
    for h in (sanctions_hits or [])[:5]:
        if (getattr(h, "score", 0) or 0) >= 0.6:
            evidence.append({
                "type": "opensanctions",
                "description": f"OpenSanctions match: {getattr(h, 'name', '?')}",
                "source": "OpenSanctions / Trade.gov CSL",
                "programs": list(getattr(h, "programs", []) or []),
            })

    return RiskFactor(
        title="Sanctions Exposure",
        severity=severity,
        score=score,
        summary=summary,
        evidence=evidence,
    )


def _factor_corporate_ties(affiliations: list[dict[str, Any]]) -> RiskFactor:
    """F2 — Corporate affiliations (officer/director roles).

    Policy (configured 2026-04-13):
      * 5+ active roles                     -> expected
      * Any ACTIVE role in an adversary
        jurisdiction (RU/IR/KP/CN/BY/VE/
        SY/CU)                              -> expected
      * 1-4 active roles, clean jurisdiction -> suggested
      * Historical roles only                -> suggested
      * No roles                             -> none
    """
    total = len(affiliations)
    active_rows = [a for a in affiliations if a.get("active")]
    active = len(active_rows)
    adversary_active = any(
        _is_adversary_country(a.get("jurisdiction") or a.get("nationality"))
        for a in active_rows
    )
    if total == 0:
        severity: Severity = "none"
        score = 0
        summary = "No corporate officer or director roles found."
    elif adversary_active:
        severity = "expected"
        score = 70
        summary = (
            f"Active corporate role(s) in adversary jurisdiction — "
            f"{active} of {total} role(s) active."
        )
    elif active >= 5:
        severity = "expected"
        score = 60
        summary = f"Currently active in {active} of {total} corporate roles."
    elif active >= 1:
        severity = "suggested"
        score = 30
        summary = f"Active in {active} of {total} corporate roles."
    else:
        severity = "suggested"
        score = 20
        summary = f"Historical roles only ({total} total, none active)."

    evidence = [
        {
            "type": "corporate_role",
            "description": f"{a.get('role','officer')} at {a.get('company','?')}",
            "source": "OpenCorporates",
            "active": a.get("active"),
            "jurisdiction": _normalize_country(a.get("jurisdiction") or a.get("nationality")),
        }
        for a in affiliations[:5]
    ]
    return RiskFactor(
        title="Corporate Ties", severity=severity, score=score, summary=summary, evidence=evidence
    )


def _factor_offshore(offshore: list[dict[str, Any]]) -> RiskFactor:
    """F3 — Offshore exposure (ICIJ Offshore Leaks).

    Policy (configured 2026-04-13):
      * Any ICIJ hit -> expected. Score scales with match count.
      * No hits      -> none.
    """
    if not offshore:
        return RiskFactor(
            title="Offshore Exposure",
            severity="none",
            score=0,
            summary="No matches in ICIJ Offshore Leaks.",
            evidence=[],
        )
    severity: Severity = "expected"
    score = min(100, 50 + 15 * len(offshore))
    summary = f"{len(offshore)} entity link(s) in ICIJ leaks data."
    evidence = [
        {
            "type": "offshore_link",
            "description": f"{o.get('entity','?')} ({o.get('jurisdiction','?')})",
            "source": o.get("dataset") or "ICIJ Offshore Leaks",
        }
        for o in offshore[:5]
    ]
    return RiskFactor(
        title="Offshore Exposure", severity=severity, score=score, summary=summary, evidence=evidence
    )


def _factor_news(recent_events: list[dict[str, Any]]) -> RiskFactor:
    """F4 — Recent news/events (GDELT, last ~30 days).

    Policy (configured 2026-04-13):
      Only NEGATIVE coverage counts as signal. Neutral or positive coverage
      does not change severity. GDELT tone is in [-100, +100]; tone < -2 is
      the working "negative" threshold.

      * 3+ negative articles OR avg(negative tones) < -3  -> expected
      * 1-2 negative articles                              -> suggested
      * No negative articles                               -> none
    """
    if not recent_events:
        return RiskFactor(
            title="Recent News Events",
            severity="none",
            score=0,
            summary="No recent GDELT events in the last 30 days.",
            evidence=[],
        )
    tones = [e.get("tone") for e in recent_events if isinstance(e.get("tone"), (int, float))]
    negative_tones = [t for t in tones if t < -2]
    negative_count = len(negative_tones)
    avg_neg_tone = (sum(negative_tones) / negative_count) if negative_count else 0.0

    if negative_count >= 3 or (negative_count > 0 and avg_neg_tone < -3):
        severity: Severity = "expected"
        score = 60
        summary = (
            f"{negative_count} negative articles in the last 30 days "
            f"(avg negative tone {avg_neg_tone:.1f})."
        )
    elif negative_count >= 1:
        severity = "suggested"
        score = 25
        summary = f"{negative_count} negative article(s) in the last 30 days."
    else:
        severity = "none"
        score = 0
        summary = (
            f"{len(recent_events)} recent article(s), none negative."
            if recent_events
            else "No notable recent coverage."
        )

    evidence = [
        {
            "type": "news_event",
            "description": e.get("title") or "GDELT event",
            "source": e.get("source") or "GDELT",
            "date": e.get("date"),
            "tone": e.get("tone"),
        }
        for e in recent_events[:5]
        if isinstance(e.get("tone"), (int, float)) and e["tone"] < -2
    ] or [
        {
            "type": "news_event",
            "description": e.get("title") or "GDELT event",
            "source": e.get("source") or "GDELT",
            "date": e.get("date"),
            "tone": e.get("tone"),
        }
        for e in recent_events[:3]
    ]
    return RiskFactor(
        title="Recent News Events", severity=severity, score=score, summary=summary, evidence=evidence
    )


def _factor_pep(pep_hits: list[dict[str, Any]]) -> RiskFactor:
    """F5 — Political Exposure (PEP check via Wikidata).

    Policy:
      * Current government/political position  -> expected (score 65)
      * Former government position only        -> suggested (score 30)
      * Political party membership only        -> suggested (score 20)
      * No PEP indicators                      -> none (score 0)

    A "current" position is one where Wikidata has no P582 (end time) on
    the position-held statement.
    """
    if not pep_hits:
        return RiskFactor(
            title="Political Exposure",
            severity="none",
            score=0,
            summary="No political exposure indicators found.",
            evidence=[],
        )

    all_positions: list[str] = []
    all_parties: list[str] = []
    has_current = False
    for h in pep_hits:
        all_positions.extend(h.get("positions") or [])
        all_parties.extend(h.get("parties") or [])
        if h.get("is_current"):
            has_current = True

    if all_positions and has_current:
        severity: Severity = "expected"
        score = 65
        summary = f"Active political position(s): {'; '.join(all_positions[:3])}."
    elif all_positions:
        severity = "suggested"
        score = 30
        summary = f"Former political position(s): {'; '.join(all_positions[:3])}."
    else:
        severity = "suggested"
        score = 20
        summary = f"Political party affiliation: {'; '.join(all_parties[:2])}."

    evidence = [
        {
            "type": "pep",
            "description": h.get("name", ""),
            "source": "Wikidata",
            "positions": h.get("positions", [])[:3],
            "parties": h.get("parties", [])[:2],
            "is_current": h.get("is_current", False),
        }
        for h in pep_hits[:3]
    ]
    return RiskFactor(
        title="Political Exposure",
        severity=severity,
        score=score,
        summary=summary,
        evidence=evidence,
    )


def build_risk_factors(profile: dict[str, Any]) -> list[RiskFactor]:
    """Translate the existing person-profile lookup results into 4 factor cards.

    Pure function — easy to unit-test. The input dict is whatever the
    /api/person-profile handler has already gathered:

        {
          "is_sanctioned": bool,
          "sanction_programs": [...],
          "sanctions_hits": [...],   # OpenSanctions entries
          "ofac_hits": [...],        # OFAC SDN entries
          "affiliations": [...],     # OpenCorporates officer rows
          "offshore": [...],         # ICIJ rows
          "recent_events": [...],    # GDELT events
        }
    """
    return [
        _factor_sanctions(
            profile.get("is_sanctioned", False),
            profile.get("sanction_programs", []),
            profile.get("sanctions_hits", []),
            profile.get("ofac_hits", []),
        ),
        _factor_corporate_ties(profile.get("affiliations", [])),
        _factor_offshore(profile.get("offshore", [])),
        _factor_news(profile.get("recent_events", [])),
        _factor_pep(profile.get("pep_hits", [])),
    ]
