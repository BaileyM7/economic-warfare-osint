"""Follow-up Q&A endpoint — grounded chat over a prior analysis context.

Extracted from src/api.py (Phase 2 Stage 3). Answers a follow-up question by
selecting a context-specific system prompt (company sanctions-impact report,
orchestrator pipeline result, vessel intel profile, or a single COA) and
replaying the conversation history against Claude. Mounted at /api with
require_auth applied at include time (see src/api.py). Behaviour is unchanged
from the original inline handler.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from src.auth import require_auth
from src.common import agent_memory
from src.common.config import config
from src.llm import get_anthropic_client as _get_anthropic_client

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["followup"])


class FollowUpMessage(BaseModel):
    role: str  # 'user' | 'assistant'
    text: str


class FollowUpRequest(BaseModel):
    question: str
    context_type: str = "orchestrator"  # 'company' | 'orchestrator' | 'vessel' | 'coa'
    # Now OPTIONAL. When present it is used verbatim — byte-for-byte the existing
    # behaviour — so today's frontend is unaffected. When absent, we hydrate it
    # from the session's working memory instead, which is what lets a client stop
    # posting the whole assessment back on every single question.
    context: dict[str, Any] = {}
    history: list[FollowUpMessage] = []
    session_id: str | None = None


class FollowUpResponse(BaseModel):
    answer: str
    # Additive: echoes the thread this answer belongs to (null when the caller
    # didn't use one).
    session_id: str | None = None


def _fmt_pct(v: Any) -> str:
    if v is None:
        return "N/A"
    return f"{float(v):+.1f}%"


def _fmt_mc(v: Any) -> str:
    if not v:
        return "N/A"
    v = float(v)
    if v >= 1e12:
        return f"${v / 1e12:.2f}T"
    if v >= 1e9:
        return f"${v / 1e9:.1f}B"
    if v >= 1e6:
        return f"${v / 1e6:.0f}M"
    return f"${v:,.0f}"


def _build_company_followup_system(ctx: dict[str, Any]) -> str:
    target = ctx.get("target", {})
    proj = ctx.get("projection", {})
    summary = proj.get("summary", {})
    comparables = ctx.get("comparables", [])
    control_comparables = ctx.get("control_comparables", [])
    narrative = ctx.get("narrative", "")
    sanctions = target.get("sanctions_status", {})
    metadata = ctx.get("metadata", {})

    # Sanctions detail
    csl_matches = sanctions.get("csl_matches", [])
    csl_lines = "\n".join(
        f"    · {m.get('name')} | Source: {m.get('source')} | Programs: {', '.join(m.get('programs', [])) or 'N/A'}"
        + (f" | Start: {m.get('start_date')}" if m.get("start_date") else "")
        for m in csl_matches[:5]
    )

    # Comparables — full detail
    comp_lines = "\n".join(
        f"  [{i + 1}] {c.get('name')} ({c.get('ticker')}) — sanctioned {str(c.get('sanction_date', ''))[:10]}"
        f"\n      Type: {c.get('sanction_type') or 'N/A'} | Sector: {c.get('sector') or 'N/A'}"
        f"\n      Context: {c.get('description') or 'N/A'}"
        for i, c in enumerate(comparables)
    )

    ctrl_lines = "\n".join(f"  - {c.get('name')} ({c.get('ticker')})" for c in control_comparables)

    day30_range = summary.get("day_30_range")
    day60_range = summary.get("day_60_range")
    day90_range = summary.get("day_90_range")
    r30 = f"({_fmt_pct(day30_range[0])} to {_fmt_pct(day30_range[1])})" if day30_range else ""
    r60 = f"({_fmt_pct(day60_range[0])} to {_fmt_pct(day60_range[1])})" if day60_range else ""
    r90 = f"({_fmt_pct(day90_range[0])} to {_fmt_pct(day90_range[1])})" if day90_range else ""

    coherence = proj.get("coherence_score")
    coherence_str = (
        f"{coherence * 100:.0f}% directional agreement" if coherence is not None else "N/A"
    )
    sourcing = metadata.get("sourcing_method", "unknown")

    return f"""You are a senior economic warfare intelligence analyst. Answer every question with a direct, confident judgment. You have the full data below — use it to give a definitive answer, not a hedge.

═══ TARGET ═══
Company:      {target.get("name")} ({target.get("ticker")})
Sector:       {target.get("sector")} | Industry: {target.get("industry")} | Country: {target.get("country")}
Price:        ${float(target.get("current_price") or 0):.2f} (day change: {_fmt_pct(target.get("change_pct"))})
Market Cap:   {_fmt_mc(target.get("market_cap"))}
Sanctioned:   {sanctions.get("is_sanctioned")}
Programs:     {", ".join(sanctions.get("programs", [])) or "None"}
Lists:        {", ".join(sanctions.get("lists", [])) or "None"}
{f"CSL Matches:{chr(10)}{csl_lines}" if csl_lines else "CSL Matches:  None"}

═══ PROJECTION (excess return vs sector ETF, based on {len(comparables)} comparable sanctions events) ═══
  60d pre-event:  {_fmt_pct(summary.get("pre_event_decline"))}
  30d post-event: {_fmt_pct(summary.get("day_30_post"))} {r30}
  60d post-event: {_fmt_pct(summary.get("day_60_post"))} {r60}
  90d post-event: {_fmt_pct(summary.get("day_90_post"))} {r90}
  Max drawdown:   {_fmt_pct(summary.get("max_drawdown"))}
  Coherence:      {coherence_str}
  Sourcing:       {sourcing}

═══ SANCTIONED COMPARABLE CASES ({len(comparables)}) ═══
{comp_lines or "  None — no comparable cases were found."}

═══ CONTROL GROUP — NON-SANCTIONED PEERS ({len(control_comparables)}) ═══
{ctrl_lines or "  None"}

═══ ANALYST NARRATIVE ═══
{narrative or "None generated."}

─── HOW TO ANSWER ───
• Lead with the answer. State your conclusion in the first sentence, then back it with numbers.
• Cite exact figures, company names, dates, and programs — don't describe the data, use it.
• When asked about trajectory or risk, state what the comparables show and commit to the most likely outcome.
• Never hedge with phrases like "it's unclear," "we can't be certain," or "more data would be needed." Make the call from what you have.
• Do NOT pad. Answer directly, then stop."""


def _build_orchestrator_followup_system(ctx: dict[str, Any]) -> str:
    query_info = ctx.get("query", {})
    scenario = ctx.get("scenario_type", "").replace("_", " ")
    exec_summary = ctx.get("executive_summary", "")
    findings = ctx.get("findings", [])
    friendly_fire = ctx.get("friendly_fire", [])
    recommendations = ctx.get("recommendations", [])
    confidence_summary = ctx.get("confidence_summary", {})
    tool_results = ctx.get("tool_results", {})

    findings_text = "\n".join(
        f"  [{f.get('confidence', '?')}] {f.get('category', '')}: {f.get('finding', '')}"
        for f in findings
    )
    ff_text = "\n".join(
        f"  - {ff.get('entity')}: {ff.get('details') or [ff.get('exposure_type'), ff.get('estimated_impact')] and ' | '.join(filter(None, [ff.get('exposure_type'), ff.get('estimated_impact')])) or '—'}"
        for ff in friendly_fire
    )
    rec_text = "\n".join(f"  {i + 1}. {r}" for i, r in enumerate(recommendations))
    conf_text = "\n".join(f"  {k}: {v}" for k, v in confidence_summary.items())

    # Serialize raw tool results, truncated to stay within context limits
    tool_results_json = json.dumps(tool_results, indent=2, default=str)
    if len(tool_results_json) > 40000:
        tool_results_json = tool_results_json[:40000] + "\n... [truncated]"

    return f"""You are a senior economic warfare intelligence analyst. Answer every question with a direct, confident judgment. You have the full pipeline data below — use it to give a definitive answer, not a hedge.

═══ ORIGINAL QUERY ═══
{query_info.get("raw_query", "N/A")}
Scenario type: {scenario}

═══ EXECUTIVE SUMMARY ═══
{exec_summary}

═══ FINDINGS ({len(findings)} total) ═══
{findings_text or "  None"}

═══ FRIENDLY FIRE ALERTS ({len(friendly_fire)}) ═══
{ff_text or "  None"}

═══ RECOMMENDATIONS ═══
{rec_text or "  None"}

═══ CONFIDENCE BY DOMAIN ═══
{conf_text or "  None"}

═══ RAW PIPELINE DATA (all tool results) ═══
{tool_results_json or "  None collected"}

─── HOW TO ANSWER ───
• Lead with the answer. State your conclusion in the first sentence, then back it with specifics from the data.
• Cite exact entities, figures, finding categories, and dates — don't describe the data, use it to make an argument.
• Commit to the most supported interpretation. Do not present two equal alternatives when the data favors one.
• Never hedge with phrases like "it's unclear," "we can't be certain," "the data is limited," or "more research is needed." Make the call from what you have.
• Do NOT pad. Answer directly, then stop."""


def _build_vessel_followup_system(ctx: dict[str, Any]) -> str:
    vessel = ctx.get("vessel", {})
    ownership = ctx.get("ownership_chain", [])
    trade = ctx.get("trade_activity", {})
    countries = ctx.get("countries_visited", [])
    narrative = ctx.get("narrative", "")
    risk_scores = ctx.get("risk_scores", {})
    sanctioned = ctx.get("is_sanctioned", False)

    # Ownership chain detail
    chain_lines = "\n".join(
        f"  [{o.get('depth', 1)}] {o.get('name')} ({o.get('entity_type', '')}) — {o.get('relationship_type', 'related')} — {o.get('country', 'Unknown')}"
        + (" [SANCTIONED]" if o.get("is_sanctioned") else "")
        + (" [PEP]" if o.get("is_pep") else "")
        for o in ownership
    )

    # Trade records
    records = trade.get("records", [])
    trade_lines = "\n".join(
        f"  {r.get('date', '?')} | {r.get('departure_country', '?')} → {r.get('arrival_country', '?')} | {r.get('commodity_category', '')} | {r.get('hs_description', r.get('hs_code', ''))}"
        for r in records[:15]
    )
    trade_countries = ", ".join(trade.get("trade_countries", []))
    hs_codes = ", ".join(
        h.get("description", h.get("code", ""))[:50] for h in trade.get("top_hs_codes", [])[:5]
    )

    cpi = risk_scores.get("cpi_score", "N/A")
    basel = risk_scores.get("basel_aml", "N/A")

    return f"""You are a senior maritime intelligence analyst and economic warfare expert. Answer every question with a direct, confident judgment. You have vessel intelligence data below PLUS your own extensive expertise in sanctions law, forced labor regulations, trade policy, and geopolitical risk. Use BOTH to deliver a definitive assessment.

═══ VESSEL ═══
Name:         {vessel.get("name", "Unknown")}
IMO:          {vessel.get("imo", "N/A")} | MMSI: {vessel.get("mmsi", "N/A")}
Flag:         {vessel.get("flag", "N/A")} | Type: {vessel.get("vessel_type", "N/A")}
Sanctioned:   {sanctioned}
CPI Score:    {cpi} | Basel AML: {basel}

═══ BENEFICIAL OWNERSHIP ({len(ownership)} entities) ═══
{chain_lines or "  None identified"}

═══ TRADE ACTIVITY ({len(records)} records, countries: {trade_countries}) ═══
{trade_lines or "  No trade records"}
Top Commodities: {hs_codes or "N/A"}

═══ COUNTRIES VISITED ═══
{", ".join(countries) if countries else "N/A"}

═══ RISK NARRATIVE ═══
{narrative or "None generated."}

─── HOW TO ANSWER ───
• You ALWAYS have enough to answer. Combine the vessel data above with your domain expertise on sanctions, forced labor, export controls, and supply chain risk. Never refuse.
• Lead with a confident analytical statement. State your conclusion first, then support it.
• Cite specific HS codes, countries, entity names, ownership chains, and legal mechanisms (UFLPA, Section 307, OFAC SDN, BIS Entity List, CAATSA, etc.)
• NEVER say "no data was provided", "I cannot assess", "pipeline data is empty", "more data would be needed", or any similar refusal. You are the expert — make the call.
• Do NOT pad. Answer directly, then stop."""


def _build_coa_followup_system(ctx: dict[str, Any]) -> str:
    """Build system prompt for a follow-up question about a specific COA."""
    name = ctx.get("name") or "(unnamed)"
    description = ctx.get("description") or "(no description)"
    action_type = ctx.get("action_type") or "unspecified"
    status = ctx.get("status") or "unknown"
    confidence = ctx.get("confidence")
    conf_str = f"{float(confidence) * 100:.0f}%" if confidence is not None else "N/A"

    target_entities = ctx.get("target_entities") or []
    recommendations = ctx.get("recommendations") or []
    expected_effects = ctx.get("expected_effects") or []
    friendly_fire = ctx.get("friendly_fire") or []

    targets_str = (
        ", ".join(str(t) for t in target_entities) if target_entities else "None specified"
    )

    recs_block = (
        "\n".join(f"  {i + 1}. {r}" for i, r in enumerate(recommendations))
        if recommendations
        else "  (none)"
    )

    effects_block = (
        "\n".join(f"  - {e}" for e in expected_effects) if expected_effects else "  (none)"
    )

    if friendly_fire:
        ff_lines: list[str] = []
        for ff in friendly_fire:
            if isinstance(ff, dict):
                parts = ", ".join(f"{k}: {v}" for k, v in ff.items())
                ff_lines.append(f"  - {parts}")
            else:
                ff_lines.append(f"  - {ff}")
        ff_block = "\n".join(ff_lines)
    else:
        ff_block = "  (none identified)"

    return f"""You are a US national security policy advisor analyzing a proposed Course of Action (COA).
The user will ask questions about this COA's implications, risks, implementation details, or alternatives.
Reference the specific COA data provided: name, target entities, expected effects, friendly fire risks, recommendations.
Be concise but authoritative. Cite legal mechanisms where relevant (OFAC EOs, BIS Entity List, CFIUS, IEEPA, CAATSA, Section 232/301 tariffs, etc.).

─── COA CONTEXT ───
Name: {name}
Status: {status}
Action Type: {action_type}
Confidence: {conf_str}

Description:
{description}

Target Entities: {targets_str}

Recommendations:
{recs_block}

Expected Effects:
{effects_block}

Friendly Fire Risks:
{ff_block}

─── HOW TO ANSWER ───
• Lead with a clear, confident analytical statement. State your conclusion first, then support it.
• Ground answers in the COA data above — cite specific target entities, effects, or risks when relevant.
• Cite legal/regulatory mechanisms by name when discussing implementation (OFAC, BIS, CFIUS, Treasury, State Dept, etc.).
• When asked about alternatives or escalation paths, be specific about trade-offs.
• NEVER refuse or say "more information would be needed" — you are the expert. Make the call.
• Keep answers tight. No padding."""


def _resolve_context(
    req: FollowUpRequest, username: str
) -> tuple[dict[str, Any], str, list[FollowUpMessage]]:
    """Work out what to ground this answer in: (context, context_type, history).

    Resolution order matters, and client-supplied context WINS:

      1. ``req.context`` non-empty -> use it verbatim. This is the existing
         contract, unchanged down to the byte, so the current frontend (which
         always posts context) behaves exactly as it does today.
      2. else ``req.session_id``  -> hydrate from working memory. ``wm.assessment``
         is an ``ImpactAssessment.model_dump(mode="json")`` — the *same shape* the
         browser posts back — so every ``_build_*_followup_system`` builder works
         on it untouched.
      3. else -> nothing to ground in; the caller gets a 400 as before.

    Same for history: the client's wins; otherwise we replay the thread's turns.
    """
    context = req.context or {}
    context_type = req.context_type
    history = req.history

    if context:
        return context, context_type, history

    if req.session_id:
        wm = agent_memory.get_working(req.session_id, username)
        if wm is not None and wm.assessment:
            context = wm.assessment
            context_type = wm.context_type or context_type
            if not history:
                history = [FollowUpMessage(role=t.role, text=t.text) for t in wm.turns]

    return context, context_type, history


@router.post("/followup", response_model=FollowUpResponse)
async def followup(req: FollowUpRequest, username: str = Depends(require_auth)) -> FollowUpResponse:
    """Answer a follow-up question grounded in the current analysis context."""
    client = _get_anthropic_client()
    if not client:
        raise HTTPException(status_code=503, detail="AI not configured")

    question = req.question.strip()
    if not question:
        raise HTTPException(status_code=400, detail="Question cannot be empty")

    context, context_type, history = _resolve_context(req, username)
    if not context:
        raise HTTPException(
            status_code=400,
            detail="No context to answer from: provide `context`, or a `session_id` for a "
            "session that has a completed analysis.",
        )

    if context_type == "company":
        system_prompt = _build_company_followup_system(context)
    elif context_type == "vessel":
        system_prompt = _build_vessel_followup_system(context)
    elif context_type == "coa":
        system_prompt = _build_coa_followup_system(context)
    else:
        system_prompt = _build_orchestrator_followup_system(context)

    # Build multi-turn message list from history + current question
    messages: list[dict[str, str]] = []
    for msg in history:
        messages.append({"role": msg.role, "content": msg.text})
    messages.append({"role": "user", "content": question})

    try:
        response = await asyncio.wait_for(
            client.messages.create(
                model=config.model,
                max_tokens=2500,
                system=system_prompt,
                messages=messages,
            ),
            timeout=60.0,
        )
        answer = response.content[0].text.strip()
    except Exception as exc:
        logger.warning("followup failed: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))

    # Record the exchange server-side whenever a thread is in play. This is what
    # makes the server's copy authoritative *while the client still thinks it owns
    # the history* — which is exactly what lets the frontend drop `context` and
    # `history` later without a flag-day change.
    if req.session_id:
        try:
            agent_memory.append_turn(
                req.session_id, username, agent_memory.Turn(role="user", text=question)
            )
            agent_memory.append_turn(
                req.session_id, username, agent_memory.Turn(role="assistant", text=answer)
            )
        except Exception as exc:  # noqa: BLE001 — memory must never fail an answer
            logger.warning("Could not record follow-up turns for %s: %s", req.session_id, exc)

    return FollowUpResponse(answer=answer, session_id=req.session_id)
