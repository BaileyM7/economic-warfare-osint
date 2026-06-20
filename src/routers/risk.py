"""Risk endpoint — focused entity risk report from the entity graph.

Extracted from src/api.py (Phase 2 Stage 3). Generates a node-level risk
assessment for an entity by fanning out to OFAC SDN, Trade.gov CSL, GLEIF
(corporate structure + ultimate parent), OpenCorporates (officers), ICIJ
Offshore Leaks, and Yahoo Finance (profile, price, 52-week range,
institutional holders, analyst consensus) in parallel, then scoring
jurisdiction/sanctions/market signals into an overall HIGH/MEDIUM/LOW level.
Mounted at /api with require_auth applied at include time (see src/api.py).
Behaviour is unchanged from the original inline handler.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import date
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from src.llm import generate_narrative as _generate_narrative
from src.tools.corporate.client import (
    gleif_get_ultimate_parent,
    gleif_search_lei,
    oc_search_companies,
    oc_search_officers,
)
from src.tools.market.client import YFinanceClient, _is_pension_or_sovereign
from src.tools.sanctions.client import OFACClient, SanctionsClient
from src.tools.screening.client import search_csl

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["risk"])


class EntityRiskReportRequest(BaseModel):
    name: str
    entity_type: str = "company"  # company, person, vessel, sanctions_list, sector, sayari
    ticker: str | None = None
    lei: str | None = None


_HIGH_RISK_COUNTRIES = {
    "RU",
    "IR",
    "KP",
    "BY",
    "CU",
    "SY",
    "VE",
    "MM",
    "SD",
    "SS",
    "CF",
    "CD",
    "IQ",
    "LB",
    "LY",
    "SO",
    "YE",
    "ZW",
}
_ELEVATED_RISK_COUNTRIES = {"CN", "HK", "TR", "AE", "SA", "PK", "NG", "UA", "UZ", "KZ"}

# Maps full country names (as returned by yfinance) to ISO-2 codes.
# GLEIF already returns ISO-2 codes. Unknown names resolve to None → risk=LOW (safe).
_COUNTRY_NAME_TO_ISO: dict[str, str] = {
    # Risk-relevant
    "china": "CN",
    "hong kong": "HK",
    "russia": "RU",
    "iran": "IR",
    "north korea": "KP",
    "belarus": "BY",
    "cuba": "CU",
    "syria": "SY",
    "venezuela": "VE",
    "myanmar": "MM",
    "burma": "MM",
    "sudan": "SD",
    "turkey": "TR",
    "united arab emirates": "AE",
    "saudi arabia": "SA",
    "pakistan": "PK",
    "nigeria": "NG",
    "ukraine": "UA",
    "uzbekistan": "UZ",
    "kazakhstan": "KZ",
    "south sudan": "SS",
    "central african republic": "CF",
    "dr congo": "CD",
    "democratic republic of the congo": "CD",
    "iraq": "IQ",
    "lebanon": "LB",
    "libya": "LY",
    "somalia": "SO",
    "yemen": "YE",
    "zimbabwe": "ZW",
    # Common yfinance country names (not risk-scored but needed for accurate display)
    "taiwan": "TW",
    "united states": "US",
    "germany": "DE",
    "japan": "JP",
    "france": "FR",
    "united kingdom": "GB",
    "netherlands": "NL",
    "south korea": "KR",
    "india": "IN",
    "singapore": "SG",
    "canada": "CA",
    "australia": "AU",
    "brazil": "BR",
    "mexico": "MX",
    "israel": "IL",
    "sweden": "SE",
    "switzerland": "CH",
    "spain": "ES",
    "italy": "IT",
}


def _country_to_iso(country: str | None) -> str | None:
    """Normalise a country string to an ISO-2 code for risk scoring.
    Returns None for unknown country names — risk scoring treats that as LOW."""
    if not country:
        return None
    c = country.strip()
    if len(c) == 2:
        return c.upper()
    return _COUNTRY_NAME_TO_ISO.get(c.lower())  # None if unknown — don't guess


@router.post("/entity-risk-report")
async def entity_risk_report(req: EntityRiskReportRequest):
    """Generate a focused risk report for an entity node from the entity graph.

    Runs all data sources in parallel:
      OFAC SDN · Trade.gov CSL · GLEIF (corporate structure + parent chain) ·
      OpenCorporates (officers) · ICIJ Offshore Leaks ·
      Yahoo Finance (profile, price, 52w range, institutional holders, analyst consensus)
    """
    name = req.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="Entity name cannot be empty")

    entity_type = req.entity_type.lower()
    ticker = (req.ticker or "").strip().upper() or None

    try:
        from datetime import datetime as _dt

        yf_client = YFinanceClient()
        ofac_client = OFACClient()
        sanctions_client = SanctionsClient()

        # ── Fire all data sources in parallel ────────────────────────────
        async def _safe(coro, default):
            try:
                return await asyncio.wait_for(coro, timeout=12.0)
            except Exception:
                return default

        tasks: dict[str, Any] = {
            "ofac": _safe(ofac_client.search(name), []),
            "csl": _safe(search_csl(name), []),
            "gleif": _safe(gleif_search_lei(name), []),
            "oc": _safe(oc_search_companies(name), []),
            # ICIJ /api/v1/search is currently 404 — skip to avoid wasted latency
        }
        if ticker:
            tasks["yf_profile"] = _safe(yf_client.get_stock_profile(ticker), None)
            tasks["yf_price"] = _safe(yf_client.get_price_data(ticker, period="1y"), None)
            tasks["yf_holders"] = _safe(yf_client.get_institutional_holders(ticker), [])
            tasks["yf_analyst"] = _safe(yf_client.get_analyst_estimate(ticker), None)

        keys = list(tasks.keys())
        results = await asyncio.gather(*[tasks[k] for k in keys])
        r = dict(zip(keys, results))

        # ── Sanctions ────────────────────────────────────────────────────
        ofac_hits = [e for e in (r["ofac"] or []) if (e.score or 0) >= 0.75]
        csl_raw = sanctions_client._csl_to_entries(r["csl"] or [])
        csl_hits = [e for e in csl_raw if (e.score or 0) >= 0.6]

        is_sanctioned = bool(ofac_hits or csl_hits)
        sanction_programs: list[str] = []
        sanction_lists: list[str] = []
        sanction_details: list[dict[str, Any]] = []

        for e in ofac_hits:
            if e.programs:
                sanction_programs.extend(e.programs)
            sanction_lists.append("OFAC SDN")
            sanction_details.append(
                {
                    "name": e.name,
                    "score": round(e.score or 0, 2),
                    "programs": (e.programs or [])[:3],
                    "remarks": (e.remarks or "")[:200] or None,
                }
            )
        for e in csl_hits:
            if e.programs:
                sanction_programs.extend(e.programs)
            sanction_lists.append("Trade.gov CSL")

        sanction_programs = list(dict.fromkeys(sanction_programs))[:6]
        sanction_lists = list(dict.fromkeys(sanction_lists))

        # ── Corporate structure (GLEIF) ───────────────────────────────────
        corporate_info: dict[str, Any] = {}
        country: str | None = None

        def _name_matches(query: str, candidate: str) -> bool:
            """Check that a GLEIF/OC result name actually corresponds to the query.

            For queries with 4+ char tokens, require at least one shared token.
            For short names (e.g. ZTE, BYD), fall back to case-insensitive substring.
            """
            q_low, c_low = query.lower(), candidate.lower()
            q_tokens = set(re.findall(r"[a-z0-9]{4,}", q_low))
            if not q_tokens:
                # Short ticker/acronym: require the full query as a word boundary match
                return bool(re.search(r"\b" + re.escape(q_low) + r"\b", c_low))
            c_tokens = set(re.findall(r"[a-z0-9]{4,}", c_low))
            return bool(q_tokens & c_tokens)

        lei_records = r.get("gleif") or []
        lei_records = [rec for rec in lei_records if _name_matches(name, rec.legal_name)]

        # Pull GLEIF structural data (LEI, status) but don't commit country yet —
        # yfinance is more authoritative for listed companies' actual HQ.
        gleif_country: str | None = None
        if lei_records:
            rec = lei_records[0]
            gleif_country = rec.country
            corporate_info = {
                "legal_name": rec.legal_name,
                "lei": rec.lei,
                "country": rec.country,
                "status": rec.status,
            }
            try:
                parent = await asyncio.wait_for(gleif_get_ultimate_parent(rec.lei), timeout=6.0)
                if parent:
                    corporate_info["ultimate_parent_lei"] = parent.parent_id
            except Exception:
                pass

        # Officers from OpenCorporates
        oc_companies = [c for c in (r.get("oc") or []) if _name_matches(name, c.name)]
        officers: list[dict[str, str]] = []
        oc_country: str | None = None
        if oc_companies:
            oc_co = oc_companies[0]
            if oc_co.jurisdiction:
                oc_country = oc_co.jurisdiction.split("_")[0].upper()
            if not corporate_info.get("legal_name"):
                corporate_info["legal_name"] = oc_co.name
            if oc_co.incorporation_date:
                corporate_info["incorporation_date"] = str(oc_co.incorporation_date)
            if oc_co.registered_address:
                corporate_info["registered_address"] = oc_co.registered_address
            if oc_co.status:
                corporate_info.setdefault("status", oc_co.status)
            for off in (oc_co.officers or [])[:5]:
                officers.append({"name": off.name, "role": off.role or ""})

        # If officers not from OC, try the officer-by-name endpoint
        if not officers:
            try:
                off_list = await asyncio.wait_for(oc_search_officers(name), timeout=8.0)
                for off in off_list[:5]:
                    officers.append({"name": off.name, "role": off.role or ""})
            except Exception:
                pass

        # ICIJ offshore connections (API currently unavailable — always empty)
        offshore_flags: list[dict[str, str]] = []

        # ── Market data (ticker) ──────────────────────────────────────────
        market_info: dict[str, Any] | None = None
        exposure: dict[str, Any] | None = None
        yf_country: str | None = None

        if ticker:
            profile = r.get("yf_profile")
            price_data = r.get("yf_price")
            holders = r.get("yf_holders") or []
            analyst = r.get("yf_analyst")

            if profile:
                yf_country = profile.country  # most authoritative for listed companies
            if profile and not corporate_info.get("legal_name"):
                corporate_info["legal_name"] = profile.name

            if price_data or profile:
                current_price = price_data.current_price if price_data else None
                change_pct = price_data.change_pct if price_data else None
                hi52 = price_data.fifty_two_week_high if price_data else None
                lo52 = price_data.fifty_two_week_low if price_data else None
                pct_from_hi = (
                    round((current_price - hi52) / hi52 * 100, 1)
                    if current_price and hi52
                    else None
                )
                market_info = {
                    "ticker": ticker,
                    "current_price": round(current_price, 2) if current_price else None,
                    "market_cap": profile.market_cap if profile else None,
                    "change_pct": round(change_pct, 2) if change_pct else None,
                    "sector": profile.sector if profile else None,
                    "industry": profile.industry if profile else None,
                    "exchange": profile.exchange if profile else None,
                    "fifty_two_week_high": round(hi52, 2) if hi52 else None,
                    "fifty_two_week_low": round(lo52, 2) if lo52 else None,
                    "pct_from_52w_high": pct_from_hi,
                    "analyst_target": analyst.target_price if analyst else None,
                    "analyst_recommendation": analyst.recommendation if analyst else None,
                    "analyst_count": analyst.num_analysts if analyst else None,
                    "description": (profile.description or "")[:300]
                    if profile and profile.description
                    else None,
                }

            # Institutional holder exposure
            if holders:
                top_holders = []
                for h in sorted(holders, key=lambda x: x.pct_held or 0, reverse=True)[:8]:
                    top_holders.append(
                        {
                            "name": h.holder_name,
                            "pct_held": round(h.pct_held * 100, 2)
                            if h.pct_held and h.pct_held < 1
                            else h.pct_held,
                            "value_usd": h.value,
                            "is_pension": _is_pension_or_sovereign(h.holder_name),
                        }
                    )
                pension_holders = [h for h in top_holders if h["is_pension"]]
                total_usd = sum(h["value_usd"] for h in top_holders if h["value_usd"])
                exposure = {
                    "top_holders": top_holders,
                    "pension_count": len(pension_holders),
                    "pension_names": [h["name"] for h in pension_holders][:3],
                    "total_institutional_usd": total_usd if total_usd else None,
                }

        # ── Resolve authoritative country ─────────────────────────────────
        # Priority: yfinance (actual HQ) > OC > GLEIF (may be a subsidiary's country)
        country = yf_country or oc_country or gleif_country
        country_iso = _country_to_iso(country)

        # ── Risk indicators ───────────────────────────────────────────────
        risk_indicators: list[dict[str, str]] = []

        risk_indicators.append(
            {
                "label": "Sanctions",
                "value": "DESIGNATED" if is_sanctioned else "Clear",
                "severity": "high" if is_sanctioned else "low",
            }
        )

        if sanction_programs:
            risk_indicators.append(
                {
                    "label": "OFAC Programs",
                    "value": ", ".join(sanction_programs[:3]),
                    "severity": "high",
                }
            )

        if country:
            sev = (
                "high"
                if country_iso in _HIGH_RISK_COUNTRIES
                else ("medium" if country_iso in _ELEVATED_RISK_COUNTRIES else "low")
            )
            risk_indicators.append({"label": "Jurisdiction", "value": country, "severity": sev})

        if corporate_info.get("status"):
            st = corporate_info["status"]
            risk_indicators.append(
                {
                    "label": "Entity Status",
                    "value": st,
                    "severity": "low" if st in ("ACTIVE", "ISSUED") else "medium",
                }
            )

        if market_info:
            if market_info.get("market_cap"):
                mc = market_info["market_cap"]
                mc_str = (
                    f"${mc / 1e12:.2f}T"
                    if mc >= 1e12
                    else (f"${mc / 1e9:.1f}B" if mc >= 1e9 else f"${mc / 1e6:.0f}M")
                )
                risk_indicators.append({"label": "Market Cap", "value": mc_str, "severity": "low"})
            if market_info.get("pct_from_52w_high") is not None:
                pct = market_info["pct_from_52w_high"]
                sev = "high" if pct < -30 else ("medium" if pct < -15 else "low")
                risk_indicators.append(
                    {
                        "label": "vs 52-Week High",
                        "value": f"{pct:+.1f}%",
                        "severity": sev,
                    }
                )
            if market_info.get("analyst_recommendation"):
                risk_indicators.append(
                    {
                        "label": "Analyst Consensus",
                        "value": market_info["analyst_recommendation"].upper(),
                        "severity": "low",
                    }
                )

        if exposure and exposure.get("pension_count", 0) > 0:
            risk_indicators.append(
                {
                    "label": "Friendly Fire",
                    "value": f"{exposure['pension_count']} US pension/sovereign fund(s) exposed",
                    "severity": "medium",
                }
            )

        # ── Overall risk level ────────────────────────────────────────────
        if is_sanctioned or country_iso in _HIGH_RISK_COUNTRIES or offshore_flags:
            risk_level = "HIGH"
        elif ofac_hits or country_iso in _ELEVATED_RISK_COUNTRIES:
            risk_level = "MEDIUM"
        else:
            risk_level = "LOW"

        # ── Narrative — feed Claude real numbers ─────────────────────────
        narrative_data: dict[str, Any] = {
            "name": name,
            "entity_type": entity_type,
            "is_sanctioned": is_sanctioned,
            "sanction_programs": sanction_programs,
            "sanction_details": sanction_details[:2],
            "country": country,
            "risk_level": risk_level,
            "officers": officers[:3],
            "offshore_connections": len(offshore_flags),
            "offshore_jurisdictions": [f["jurisdiction"] for f in offshore_flags[:3]],
        }
        if market_info:
            narrative_data["market_cap"] = market_info.get("market_cap")
            narrative_data["sector"] = market_info.get("sector")
            narrative_data["industry"] = market_info.get("industry")
            narrative_data["current_price"] = market_info.get("current_price")
            narrative_data["pct_from_52w_high"] = market_info.get("pct_from_52w_high")
            narrative_data["analyst_target"] = market_info.get("analyst_target")
            narrative_data["analyst_recommendation"] = market_info.get("analyst_recommendation")
        if exposure:
            narrative_data["pension_funds_exposed"] = exposure.get("pension_names")
            narrative_data["total_institutional_usd"] = exposure.get("total_institutional_usd")

        narrative_prompt = (
            f"You are an economic warfare intelligence analyst. Write a 4-6 sentence risk "
            f"assessment for '{name}' (type: {entity_type}) as of {date.today().isoformat()}. "
            f"Be specific: cite real numbers from the data (market cap, price vs 52-week high, "
            f"analyst target, pension fund names, sanction programs, offshore jurisdictions). "
            f"Cover: (1) sanctions/designation status with any specific program names, "
            f"(2) if publicly traded — where the stock sits relative to 52-week range and what "
            f"analyst consensus implies about trajectory, "
            f"(3) any offshore/corporate structure risks, "
            f"(4) friendly-fire exposure to US/allied institutional investors if applicable. "
            f"If data is sparse, state what the absence of derogatory findings means. "
            f"Do not invent numbers. Use only what is in the data below.\n"
            f"Data: {json.dumps(narrative_data)}"
        )
        narrative = await _generate_narrative(narrative_prompt)

        sources = ["OFAC SDN", "Trade.gov CSL"]
        if lei_records:
            sources.append("GLEIF")
        if oc_companies or officers:
            sources.append("OpenCorporates")
        if ticker and market_info:
            sources.append("Yahoo Finance")

        return JSONResponse(
            content={
                "name": name,
                "entity_type": entity_type,
                "risk_level": risk_level,
                "is_sanctioned": is_sanctioned,
                "sanction_programs": sanction_programs,
                "sanction_lists": sanction_lists,
                "sanction_details": sanction_details[:3],
                "country": country,
                "corporate_info": corporate_info,
                "officers": officers,
                "offshore_flags": offshore_flags,
                "market_info": market_info,
                "exposure": exposure,
                "risk_indicators": risk_indicators,
                "narrative": narrative,
                "sources": sources,
                "generated_at": _dt.utcnow().isoformat() + "Z",
            }
        )

    except Exception as e:
        logger.exception("entity_risk_report error for name=%s", name)
        raise HTTPException(status_code=500, detail=str(e))
