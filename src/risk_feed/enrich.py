"""Click-time enrichment for Risk Feed cards.

Reason this exists: every card-builder in src/tools/sanctions/delta.py and
src/tools/markets/feed.py emits exactly ONE entry in synthetic_payload.sources_used.
The COA prompt at src/routers/coa.py:224 requires the rationale to contain >=2
distinct [N] markers. With only [1] available the LLM either repeats [1],
hallucinates a [2], or drops citations — none of which produce a defensible
analyst-grade COA.

This module enriches the synthetic_payload at click time (not refresh time) so
we only pay for cards a user actually opens. Each per-category enrichment fans
out 2-3 cached helper calls in parallel and merges their results into
sources_used / key_findings, leaving the rest of the payload untouched.

Best-effort by design: any helper that fails is logged and skipped; the
caller will always get a payload back, even if enrichment added nothing.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from src.tools.geopolitical.client import gdelt_doc_search
from src.tools.market.client import YFinanceClient
from src.tools.sanctions.client import OFACClient
from src.tools.screening.client import search_csl, search_pep

logger = logging.getLogger(__name__)


def _existing_urls(payload: dict[str, Any]) -> set[str]:
    """URLs already present in sources_used — used to dedupe new entries."""
    out: set[str] = set()
    for s in payload.get("sources_used") or []:
        url = (s or {}).get("url")
        if url:
            out.add(url)
        rec = (s or {}).get("record_url")
        if rec:
            out.add(rec)
    return out


def _gdelt_tone_band(t: float | None) -> str | None:
    if t is None:
        return None
    if t <= -5.0:
        return "strongly negative"
    if t <= -3.5:
        return "negative"
    if t <= -2.0:
        return "moderately negative"
    if t < 0:
        return "mildly negative"
    return "neutral or positive"


# --- Helper coroutines (each returns a list of source dicts) ---


async def _enrich_gdelt(
    query: str, label: str, days: int = 30, max_records: int = 5
) -> tuple[list[dict[str, Any]], int]:
    """Pull GDELT articles; return (sources, article_count). Empty on failure."""
    try:
        events = await gdelt_doc_search(query, days=days, max_records=max_records)
    except Exception as exc:
        logger.warning("enrich gdelt failed for %r: %s", query, exc)
        return [], 0
    if not events:
        return [], 0
    sources: list[dict[str, Any]] = []
    seen: set[str] = set()
    # Prefer worst-tone first when tone is available; otherwise document order.
    scored = [e for e in events if getattr(e, "avg_tone", None) is not None]
    if scored:
        scored.sort(key=lambda e: e.avg_tone or 0.0)
        ordered = scored + [e for e in events if e not in scored]
    else:
        ordered = list(events)
    for ev in ordered[:3]:
        url = getattr(ev, "source_url", "") or ""
        if not url or url in seen:
            continue
        seen.add(url)
        tone = getattr(ev, "avg_tone", None)
        band = _gdelt_tone_band(tone)
        desc_parts = [f"GDELT 2.0 article on '{query}'"]
        if band:
            desc_parts.append(f"sentiment {band}")
        sources.append(
            {
                "name": f"GDELT article — {label}",
                "url": url,
                "description": ", ".join(desc_parts),
            }
        )
    return sources, len(events)


async def _enrich_csl(name: str) -> list[dict[str, Any]]:
    """Look up the entity on Trade.gov CSL; emit one source per matching list."""
    try:
        hits = await search_csl(name, limit=5)
    except Exception as exc:
        logger.warning("enrich csl failed for %r: %s", name, exc)
        return []
    out: list[dict[str, Any]] = []
    seen_lists: set[str] = set()
    for hit in hits or []:
        source_list = (hit.get("source") or "").strip()
        if not source_list or source_list in seen_lists:
            continue
        seen_lists.add(source_list)
        url = hit.get("source_list_url") or "https://www.trade.gov/consolidated-screening-list"
        programs = hit.get("programs") or []
        if isinstance(programs, list):
            program_str = ", ".join(p for p in programs if p)
        else:
            program_str = str(programs)
        out.append(
            {
                "name": f"Trade.gov CSL — {source_list}",
                "url": url,
                "description": (
                    f"Cross-listed on {source_list}"
                    + (f" under {program_str}" if program_str else "")
                ),
            }
        )
    return out


async def _enrich_ofac_search(name: str) -> list[dict[str, Any]]:
    """Cross-check whether the entity name appears on OFAC SDN."""
    try:
        client = OFACClient()
        hits = await client.search(name)
    except Exception as exc:
        logger.warning("enrich ofac failed for %r: %s", name, exc)
        return []
    out: list[dict[str, Any]] = []
    for hit in (hits or [])[:1]:  # one cross-list hit is enough
        ent_id = getattr(hit, "id", "") or ""
        programs = getattr(hit, "programs", None) or []
        program_str = ", ".join(programs) if isinstance(programs, list) else str(programs)
        out.append(
            {
                "name": "OFAC SDN — cross-list match",
                "url": "https://www.treasury.gov/ofac/downloads/sdn.csv",
                "record_url": (
                    f"https://sanctionssearch.ofac.treas.gov/Details.aspx?id={ent_id}"
                    if ent_id
                    else None
                ),
                "description": (
                    f"OFAC SDN match for '{name}'"
                    + (f" under {program_str}" if program_str else "")
                ),
            }
        )
    return [{k: v for k, v in s.items() if v} for s in out]


async def _enrich_pep(name: str) -> list[dict[str, Any]]:
    """Wikidata PEP check for individuals."""
    try:
        records = await search_pep(name, limit=2)
    except Exception as exc:
        logger.warning("enrich pep failed for %r: %s", name, exc)
        return []
    out: list[dict[str, Any]] = []
    for rec in records or []:
        wiki_id = rec.get("wikidata_id") or ""
        positions = rec.get("positions") or []
        countries = rec.get("countries") or []
        url = f"https://www.wikidata.org/wiki/{wiki_id}" if wiki_id else "https://www.wikidata.org"
        desc_parts = ["Wikidata PEP record"]
        if positions:
            desc_parts.append(f"positions: {', '.join(positions[:3])}")
        if countries:
            desc_parts.append(f"country: {', '.join(countries[:2])}")
        out.append(
            {
                "name": f"Wikidata PEP — {rec.get('name') or name}",
                "url": url,
                "description": "; ".join(desc_parts),
            }
        )
    return out


async def _enrich_yfinance_profile(ticker: str) -> tuple[list[dict[str, Any]], list[str]]:
    """Pull the structured StockProfile; return (sources, extra_findings)."""
    try:
        client = YFinanceClient()
        profile = await client.get_stock_profile(ticker)
    except Exception as exc:
        logger.warning("enrich yfinance profile failed for %r: %s", ticker, exc)
        return [], []
    extra_findings: list[str] = []
    if getattr(profile, "sector", None):
        extra_findings.append(f"Sector: {profile.sector}")
    if getattr(profile, "industry", None):
        extra_findings.append(f"Industry: {profile.industry}")
    if getattr(profile, "country", None):
        extra_findings.append(f"Country of incorporation: {profile.country}")
    sources = [
        {
            "name": f"yfinance profile — {ticker}",
            "url": f"https://finance.yahoo.com/quote/{ticker}/profile",
            "description": "Company profile (sector / industry / country) from Yahoo Finance",
        }
    ]
    return sources, extra_findings


# --- Per-category dispatchers ---


def _detect_card_type(item: dict[str, Any]) -> str:
    """Map a feed item to one of the enrichment recipe types."""
    item_id = item.get("id", "")
    if item_id.startswith("ofac-"):
        if item.get("category") == "people_sanctions":
            return "ofac_individual"
        return "ofac_entity"
    if item_id.startswith("csl-"):
        return "csl_entity"
    if item_id.startswith("yf-"):
        return "yfinance"
    if item_id.startswith("gdelt-region-"):
        return "gdelt_region"
    if item_id.startswith("gdelt-ent-"):
        return "gdelt_entity"
    return "unknown"


def _merge(
    payload: dict[str, Any],
    new_sources: list[dict[str, Any]],
    extra_findings: list[str] | None = None,
) -> None:
    """Mutate payload in place: append new sources (deduped by URL) and findings."""
    seen = _existing_urls(payload)
    sources = list(payload.get("sources_used") or [])
    for s in new_sources:
        url = s.get("url") or s.get("record_url") or ""
        if url and url in seen:
            continue
        if url:
            seen.add(url)
        sources.append(s)
    payload["sources_used"] = sources
    if extra_findings:
        kf = list(payload.get("key_findings") or [])
        for f in extra_findings:
            if f and f not in kf:
                kf.append(f)
        payload["key_findings"] = kf


async def enrich_payload(item: dict[str, Any]) -> dict[str, Any]:
    """Return an enriched copy of item['synthetic_payload'].

    Best-effort: helper failures are logged and ignored. Always returns a
    payload (the original on total failure).
    """
    base = dict(item.get("synthetic_payload") or {})
    base["sources_used"] = list(base.get("sources_used") or [])
    base["key_findings"] = list(base.get("key_findings") or [])

    name = item.get("entity") or ""
    card_type = _detect_card_type(item)

    # Build the enrichment plan per card type.
    plan: list[tuple[str, Any]] = []
    if card_type == "ofac_entity":
        plan = [
            ("gdelt", _enrich_gdelt(name, name)),
            ("csl", _enrich_csl(name)),
        ]
    elif card_type == "ofac_individual":
        plan = [
            ("gdelt", _enrich_gdelt(name, name)),
            ("pep", _enrich_pep(name)),
        ]
    elif card_type == "csl_entity":
        plan = [
            ("gdelt", _enrich_gdelt(name, name)),
            ("ofac", _enrich_ofac_search(name)),
        ]
    elif card_type == "gdelt_entity":
        plan = [
            ("csl", _enrich_csl(name)),
            ("ofac", _enrich_ofac_search(name)),
        ]
    elif card_type == "gdelt_region":
        # Region cards already pack 3 sources via Change A; pull one more
        # GDELT slice on a related macro angle.
        plan = [
            ("gdelt", _enrich_gdelt(name, name, days=14, max_records=3)),
        ]
    elif card_type == "yfinance":
        ticker = item.get("id", "").replace("yf-", "").split("-")[0]
        plan = [
            ("gdelt", _enrich_gdelt(name, name, days=7, max_records=5)),
            (
                "yfinance_profile",
                _enrich_yfinance_profile(ticker) if ticker else asyncio.sleep(0, result=([], [])),
            ),
        ]
    else:
        return base  # nothing we can do for unknown card types

    labels = [label for label, _ in plan]
    coros = [coro for _, coro in plan]
    results = await asyncio.gather(*coros, return_exceptions=True)

    extra_findings_total: list[str] = []
    article_count_for_findings: int | None = None

    for label, result in zip(labels, results):
        if isinstance(result, Exception):
            logger.warning("enrichment task '%s' raised: %s", label, result)
            continue
        if label == "gdelt":
            sources, count = result  # tuple
            _merge(base, sources)
            if count and article_count_for_findings is None:
                article_count_for_findings = count
        elif label == "yfinance_profile":
            sources, findings = result  # tuple
            _merge(base, sources, findings)
        else:
            # csl, ofac, pep — flat source lists
            _merge(base, result)

    if article_count_for_findings is not None:
        extra_findings_total.append(
            f"GDELT enrichment: {article_count_for_findings} related articles found"
        )
    if extra_findings_total:
        _merge(base, [], extra_findings_total)

    return base
