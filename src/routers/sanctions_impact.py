"""Sanctions-impact endpoint — projected stock price impact from sanctions.

Extracted from src/api.py (Phase 2 Stage 3). Runs the sanctions-impact
projector for a ticker (price trajectory modelled from historical comparable
sanctions events, expressed as market-adjusted excess returns vs the sector
ETF benchmark), then asks Claude for a plain-language risk narrative and a set
of course-of-action recommendations. Mounted at /api with require_auth applied
at include time (see src/api.py). Behaviour is unchanged from the original
inline handler.
"""

from __future__ import annotations

import asyncio
import json
from datetime import date

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from src.llm import generate_narrative as _generate_narrative
from src.llm import generate_recommendations as _generate_recommendations
from src.sanctions_impact import run_sanctions_impact

router = APIRouter(prefix="/api", tags=["sanctions-impact"])


class SanctionsImpactRequest(BaseModel):
    ticker: str
    analyst_question: str = ""  # original user query for context-aware CoA


def _build_narrative_prompt(compact: dict, comp_count: int) -> str:
    """Build the risk-narrative prompt, with guardrails against two real failure
    modes observed on Chinese ADRs (e.g. Alibaba/BABA):

      1. `listing_region` comes from the market-data provider's country field, which
         reflects the PRIMARY LISTING/EXCHANGE — not the legal domicile (Finnhub tags
         BABA "HK", but Alibaba is Cayman-incorporated and HQ'd in mainland China).
         Without this guard the model wrote "its Hong Kong domicile" — factually wrong.
      2. `comparables_avg_pre_event_decline_pct` is the AVERAGE sector-relative move of
         the historical comparable cases before their event — NOT the target's own
         price history. Without this guard the model claimed that decline was "already
         baked into the share price."
    """
    return (
        f"You are an economic warfare analyst. Given the following data about "
        f"{compact['name']} ({compact['ticker']}), write a 3-5 sentence risk narrative covering: "
        f"(1) current sanctions status, (2) likely stock price trajectory — note the "
        f"projection uses market-adjusted excess returns vs the sector ETF benchmark, so "
        f"values reflect underperformance vs the sector, not necessarily absolute price "
        f"declines; translate to plain language for the reader, (3) key supply chain or "
        f"investor exposure that constitutes friendly fire risk.\n"
        f"Data: {json.dumps(compact)}\n"
        f"IMPORTANT — do not misstate these two fields:\n"
        f"- `listing_region` is the PRIMARY LISTING / EXCHANGE region, which can differ from "
        f"the company's legal domicile or HQ (many US-listed Chinese ADRs are Cayman-incorporated "
        f"and HQ'd in mainland China). Do NOT assert a legal domicile or country of incorporation "
        f"from this field; refer to it only as the listing/operating region.\n"
        f"- `comparables_avg_pre_event_decline_pct` is the AVERAGE sector-relative pre-event move "
        f"of the historical comparable cases — it is NOT this company's own actual price history. "
        f"Do NOT claim that decline is 'already baked into' the target's share price.\n"
        f"Confidence qualifier: {comp_count} comparable cases used, data as of "
        f"{date.today().isoformat()}."
    )


@router.post("/sanctions-impact")
async def sanctions_impact(req: SanctionsImpactRequest):
    """Project stock price impact from sanctions based on historical comparables."""
    ticker = req.ticker.strip().upper()
    if not ticker:
        raise HTTPException(status_code=400, detail="Ticker cannot be empty")

    try:
        result = await run_sanctions_impact(ticker)

        # Build narrative prompt from result data
        target = result.get("target", {})
        summary = result.get("projection", {}).get("summary", {})
        comp_count = result.get("metadata", {}).get("comparable_count", 0)
        is_sanctioned = target.get("sanctions_status", {}).get("is_sanctioned", False)
        programs = target.get("sanctions_status", {}).get("programs", [])
        compact = {
            "name": target.get("name", ticker),
            "ticker": ticker,
            "sector": target.get("sector"),
            # NOTE: the provider's country field is the primary listing/exchange region,
            # not the legal domicile — named accordingly so the LLM can't misread it.
            "listing_region": target.get("country"),
            "is_sanctioned": is_sanctioned,
            "sanction_programs": programs,
            # The comparables' AVERAGE pre-event move, not the target's own price history.
            "comparables_avg_pre_event_decline_pct": summary.get("pre_event_decline"),
            "day_30_post_pct": summary.get("day_30_post"),
            "day_90_post_pct": summary.get("day_90_post"),
            "max_drawdown_pct": summary.get("max_drawdown"),
        }
        prompt = _build_narrative_prompt(compact, comp_count)
        narrative, recommendations = await asyncio.gather(
            _generate_narrative(prompt),
            _generate_recommendations(compact, req.analyst_question),
        )
        result["narrative"] = narrative
        result["recommendations"] = recommendations
        return JSONResponse(content=result)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
