"""Sanctions feed builder for the Risk Feed POC.

Replaces the earlier delta-only approach (which went empty after the first
refresh) with a *recent + severity-ranked* surface: it always returns cards by
sorting the OFAC SDN list by recency (ent_num as a proxy) and scoring each
entry by program weight, then optionally augments with Trade.gov CSL hits on a
small watch-keyword list to pick up Entity List / Unverified List / MEU
designations that aren't on the SDN.

Reuses the existing OFACClient download path (24-hr disk cache) and search_csl
client — no new HTTP infrastructure.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from src.tools.sanctions.client import OFACClient
from src.tools.screening.client import search_csl

logger = logging.getLogger(__name__)

# Map raw OFAC sdnType -> our feed category. The SDN CSV ships these
# *lowercased* (`individual`, `vessel`, `aircraft`) and uses the literal
# string `-0-` as the placeholder for **Entity** (companies / organizations
# get no explicit type tag). Documented Treasury values stay as a backstop
# in case the format ever changes.
_TYPE_TO_CATEGORY = {
    # Lowercase as actually shipped today
    "individual": "people_sanctions",
    "vessel": "markets",
    "aircraft": "markets",
    # OFAC's null-placeholder == Entity (~9,600 rows / >50% of the SDN list)
    "-0-": "company_sanctions",
    "": "company_sanctions",
    # Capitalized backstops for forward-compat
    "Individual": "people_sanctions",
    "Entity": "company_sanctions",
    "entity": "company_sanctions",
    "Vessel": "markets",
    "Aircraft": "markets",
}

# Program-code severity weights. Higher = more analyst-relevant for a card feed.
_PROGRAM_WEIGHTS: dict[str, int] = {
    "SDGT": 100,  # Specially Designated Global Terrorist
    "IRGC": 95,
    "DPRK": 95,
    "CYBER": 90,
    "CYBER2": 90,
    "TERROR": 90,
    "RUSSIA-EO14024": 85,
    "RUSSIA-EO13662": 80,
    "IRAN-EO13902": 80,
    "IRAN": 75,
    "GLOMAG": 75,
    "VENEZUELA": 70,
    "MAGNIT": 70,
    "ELN": 65,
    "FENTANYL": 70,
    "NARCOTICS": 65,
    "HUMAN-RIGHTS": 65,
    "BURMA-EO14014": 60,
    "SYRIA": 60,
    "CUBA": 50,
}

# Suggested CSL keyword queries surfaced as starter items in the empty-state
# UI. After Phase 3, the active queries used at refresh time come from each
# user's watchlist_items rows where entity_kind='sanctions_keyword'.
SUGGESTED_CSL_KEYWORDS: list[str] = [
    "semiconductor",
    "drone",
    "missile",
    "nuclear",
    "Huawei",
    "Rosneft",
    "Iran",
    "Wagner",
]

# How many cards we cap per category per refresh.
_CAP_ENTITY = 8
_CAP_INDIVIDUAL = 6
_CAP_VESSEL = 4

# How far back from the most-recent SDN ent_num we sweep when scoring.
_RECENCY_WINDOW = 250


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _program_weight(program_str: str) -> int:
    """Compute the max program weight from an OFAC program field
    (which is sometimes ' ; ' delimited, e.g. 'SDGT; IFSR')."""
    if not program_str:
        return 30
    parts = [p.strip().upper() for p in program_str.replace(";", ",").split(",") if p.strip()]
    if not parts:
        return 30
    weights = []
    for part in parts:
        # Allow prefix match — e.g. "RUSSIA-EO14024" has a fully-qualified weight
        # but a plain "RUSSIA" should also score.
        if part in _PROGRAM_WEIGHTS:
            weights.append(_PROGRAM_WEIGHTS[part])
            continue
        for key, w in _PROGRAM_WEIGHTS.items():
            if part.startswith(key) or key.startswith(part):
                weights.append(w)
                break
        else:
            weights.append(40)  # default for any non-matched program
    return max(weights)


def _severity_from_score(score: int) -> str:
    if score >= 85:
        return "high"
    if score >= 60:
        return "medium"
    return "low"


def _build_synthetic_payload_ofac(entry: dict[str, str], address: str | None) -> dict[str, Any]:
    """`analysis_data`-shaped payload for /coa/generate from an OFAC SDN row."""
    name = entry.get("name", "").strip()
    program = entry.get("program", "").strip()
    sdn_type = entry.get("type", "").strip()
    ent_num = entry.get("ent_num", "").strip()
    title = entry.get("title", "").strip()
    remarks = entry.get("remarks", "").strip()

    findings: list[str] = []
    if program:
        findings.append(f"SDN program codes: {program}")
    if address:
        findings.append(f"Listed address: {address}")
    if title:
        findings.append(f"Title/role: {title}")
    if remarks:
        findings.append(f"Treasury remarks: {remarks[:280]}")
    findings.append(f"OFAC entity number: {ent_num}")

    if sdn_type == "Individual":
        summary = (
            f"{name} appears on the OFAC SDN list under {program or 'an active sanctions program'}. "
            f"Treasury record cites {title or 'role/affiliation'} as the basis for the designation."
        )
    elif sdn_type == "Entity":
        summary = (
            f"{name} is on the OFAC SDN list under {program or 'an active sanctions program'}. "
            "U.S. persons are barred from transacting with it; any U.S.-nexus assets are blocked."
        )
    else:
        summary = (
            f"{name} ({sdn_type.lower() or 'entry'}) is on the OFAC SDN list under "
            f"{program or 'an active sanctions program'}."
        )

    sources = [
        {
            "name": "OFAC SDN List",
            "url": "https://www.treasury.gov/ofac/downloads/sdn.csv",
            "record_url": (
                f"https://sanctionssearch.ofac.treas.gov/Details.aspx?id={ent_num}"
                if ent_num
                else None
            ),
            "description": (
                f"U.S. Treasury SDN — {sdn_type or 'entry'} listed under "
                f"{program or 'sanctions program'}"
            ),
        }
    ]
    sources = [{k: v for k, v in s.items() if v} for s in sources]

    return {
        "scenario_type": "sanctions_designation",
        "executive_summary": summary,
        "target_entities": [name] if name else [],
        "key_findings": findings,
        "sources_used": sources,
        "confidence": 0.9,
    }


def _ofac_entry_to_item(
    entry: dict[str, str],
    address: str | None,
    score: int,
) -> dict[str, Any] | None:
    name = entry.get("name", "").strip()
    sdn_type = entry.get("type", "").strip()
    ent_num = entry.get("ent_num", "").strip()
    program = entry.get("program", "").strip()
    if not name or not ent_num:
        return None
    category = _TYPE_TO_CATEGORY.get(sdn_type)
    if category is None:
        return None
    headline = f"OFAC SDN: {name}"
    if program:
        headline += f" — {program.split(';')[0].strip()}"
    return {
        "id": f"ofac-{ent_num}",
        "category": category,
        "severity": _severity_from_score(score),
        "headline": headline,
        "entity": name,
        "source_url": f"https://sanctionssearch.ofac.treas.gov/Details.aspx?id={ent_num}",
        # OFAC SDN CSV doesn't carry a per-row designation date; the Risk Feed
        # card shows fetch time only. Phase 3.5 will pull this from
        # OpenSanctions enrichment.
        "event_at": None,
        "fetched_at": _now_iso(),
        "synthetic_payload": _build_synthetic_payload_ofac(entry, address),
        "_score": score,  # private; risk_feed router strips before returning
    }


def _build_synthetic_payload_csl(hit: dict[str, Any]) -> dict[str, Any]:
    name = (hit.get("name") or "").strip()
    source = (hit.get("source") or "").strip()
    programs = hit.get("programs") or []
    if isinstance(programs, list):
        programs_str = ", ".join(p for p in programs if p)
    else:
        programs_str = str(programs)
    addresses = hit.get("addresses") or []
    address = ""
    if isinstance(addresses, list) and addresses:
        first = addresses[0]
        if isinstance(first, dict):
            parts = [first.get(k) for k in ("address", "city", "country") if first.get(k)]
            address = ", ".join(parts)
        elif isinstance(first, str):
            address = first

    findings: list[str] = []
    if programs_str:
        findings.append(f"Programs: {programs_str}")
    if address:
        findings.append(f"Listed address: {address}")
    if hit.get("start_date"):
        findings.append(f"Designation start date: {hit['start_date']}")
    if hit.get("remarks"):
        findings.append(f"Remarks: {str(hit['remarks'])[:280]}")

    summary = (
        f"{name} appears on a U.S. consolidated screening list ({source}) under "
        f"{programs_str or 'an active program'}. Restrictions vary by source list and end-use."
    )

    sources = [
        {
            "name": f"Trade.gov CSL — {source}" if source else "Trade.gov CSL",
            "url": hit.get("source_list_url")
            or "https://www.trade.gov/consolidated-screening-list",
            "description": f"Consolidated Screening List entry from {source or 'CSL'}",
        }
    ]

    return {
        "scenario_type": "sanctions_designation",
        "executive_summary": summary,
        "target_entities": [name] if name else [],
        "key_findings": findings,
        "sources_used": sources,
        "confidence": 0.82,
    }


def _csl_hit_to_item(hit: dict[str, Any]) -> dict[str, Any] | None:
    name = (hit.get("name") or "").strip()
    source = (hit.get("source") or "").strip()
    if not name:
        return None

    raw_type = (hit.get("type") or "").strip().lower()
    if raw_type == "individual":
        category = "people_sanctions"
    else:
        # Most CSL entries beyond SDN (Entity List, UVL, MEU, NS-CMIC) are entities.
        category = "company_sanctions"

    programs = hit.get("programs") or []
    if isinstance(programs, list):
        primary_program = programs[0] if programs else ""
    else:
        primary_program = str(programs)

    score = _program_weight(primary_program)
    if source.upper() in {"ENTITY LIST", "MILITARY END USER (MEU) LIST"}:
        score = max(score, 75)

    headline = f"CSL: {name}"
    if source:
        headline += f" — {source}"

    item_id = f"csl-{abs(hash((name, source, primary_program))) % 10_000_000}"

    # CSL entries often carry a designation start_date (ISO YYYY-MM-DD) — the
    # most accurate event_at we can offer for sanctions cards in Phase 3.
    raw_start = hit.get("start_date")
    event_at = raw_start.strip() if isinstance(raw_start, str) and raw_start.strip() else None

    return {
        "id": item_id,
        "category": category,
        "severity": _severity_from_score(score),
        "headline": headline,
        "entity": name,
        "source_url": hit.get("source_list_url")
        or "https://www.trade.gov/consolidated-screening-list",
        "event_at": event_at,
        "fetched_at": _now_iso(),
        "synthetic_payload": _build_synthetic_payload_csl(hit),
        "_score": score,
    }


def _rank_and_cap(
    items: list[dict[str, Any]],
    cap: int,
) -> list[dict[str, Any]]:
    """Sort by descending _score then by id (deterministic tiebreak), cap, and
    drop the private _score field."""
    items.sort(key=lambda it: (-int(it.get("_score", 0)), it.get("id", "")))
    out = items[:cap]
    for it in out:
        it.pop("_score", None)
    return out


async def build_sanctions_feed(
    csl_keywords: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Build the company-/people-sanctions slice of the Risk Feed.

    Strategy:
      1. Load OFAC SDN, take the most-recent _RECENCY_WINDOW entries by
         ent_num, score by program weight, split per category, cap.
         (Always runs — global ranked surface, not driven by user watch-list.)
      2. Augment with Trade.gov CSL hits using `csl_keywords`. Empty list =
         skip CSL augmentation entirely (legitimate when the user hasn't
         added any sanctions-keyword watch-list items). None = use
         SUGGESTED_CSL_KEYWORDS — back-compat for callers that haven't
         migrated yet.
      3. Dedupe (CSL hits that match an already-surfaced OFAC entity drop).
    """
    import asyncio

    csl_keywords = csl_keywords if csl_keywords is not None else SUGGESTED_CSL_KEYWORDS

    client = OFACClient()
    await client._ensure_loaded()
    entries: list[dict[str, str]] = client._sdn_entries or []
    addresses: dict[str, list[str]] = client._addresses or {}

    # 1. OFAC recent + severity-ranked
    sorted_entries = sorted(
        entries,
        key=lambda e: int(e.get("ent_num", "0")) if e.get("ent_num", "").isdigit() else 0,
        reverse=True,
    )[:_RECENCY_WINDOW]

    bucket_entity: list[dict[str, Any]] = []
    bucket_individual: list[dict[str, Any]] = []
    bucket_vessel: list[dict[str, Any]] = []
    for entry in sorted_entries:
        score = _program_weight(entry.get("program", ""))
        # Recency boost: top 50 most recent get +5
        if entry in sorted_entries[:50]:
            score += 5
        addr_list = addresses.get(entry.get("ent_num", "").strip()) or []
        item = _ofac_entry_to_item(entry, addr_list[0] if addr_list else None, score)
        if not item:
            continue
        if item["category"] == "people_sanctions":
            bucket_individual.append(item)
        elif item["category"] == "company_sanctions":
            bucket_entity.append(item)
        else:
            bucket_vessel.append(item)

    items: list[dict[str, Any]] = []
    items += _rank_and_cap(bucket_entity, _CAP_ENTITY)
    items += _rank_and_cap(bucket_individual, _CAP_INDIVIDUAL)
    items += _rank_and_cap(bucket_vessel, _CAP_VESSEL)

    seen_names: set[str] = {it["entity"].lower() for it in items}

    # 2. CSL augmentation (gated on api key inside search_csl + on user-supplied keywords)
    csl_tasks = [search_csl(q, limit=10, sources="Entity List") for q in csl_keywords]
    csl_results = await asyncio.gather(*csl_tasks, return_exceptions=True)

    csl_items: list[dict[str, Any]] = []
    for r in csl_results:
        if isinstance(r, Exception):
            logger.warning("CSL augmentation task raised: %s", r)
            continue
        for hit in r or []:
            item = _csl_hit_to_item(hit)
            if not item:
                continue
            if item["entity"].lower() in seen_names:
                continue
            seen_names.add(item["entity"].lower())
            csl_items.append(item)

    csl_items = _rank_and_cap(csl_items, 6)
    items += csl_items

    return items


# Back-compat shim so existing callers don't break while we transition.
async def detect_ofac_delta() -> list[dict[str, Any]]:
    return await build_sanctions_feed()
