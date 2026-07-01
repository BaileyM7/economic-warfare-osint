"""Shared Anthropic LLM client + the standard narrative / CoA generators.

These live here (not in api.py) so both api.py and the routers can import them
without a circular dependency. Extracted from api.py in Phase 2 Stage 3 with
behaviour unchanged.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re

import anthropic

from src.common.config import config

logger = logging.getLogger(__name__)

_anthropic_client: anthropic.AsyncAnthropic | None = None

# The Anthropic SDK retries 429/408/5xx (including 529 "Overloaded") with
# exponential backoff. We bump this above the default of 2 so transient
# capacity spikes don't surface as hard errors for the user.
_MAX_RETRIES = 4


def get_anthropic_client() -> anthropic.AsyncAnthropic | None:
    global _anthropic_client
    if _anthropic_client is None and config.anthropic_api_key:
        _anthropic_client = anthropic.AsyncAnthropic(
            api_key=config.anthropic_api_key,
            max_retries=_MAX_RETRIES,
        )
    return _anthropic_client


async def generate_narrative(prompt: str) -> str:
    """Generate a 3–5 sentence analyst narrative. Returns '' on any failure.

    Routed through the pluggable text provider (issue #33) so it works against
    Anthropic or a local OpenAI-compatible model with no call-site changes.
    """
    from src.common.llm_provider import get_text_provider

    provider = get_text_provider()
    if not provider:
        return ""
    try:
        text = await asyncio.wait_for(
            provider.complete(
                system="",
                messages=[{"role": "user", "content": prompt}],
                max_tokens=500,
            ),
            timeout=15.0,
        )
        return (text or "").strip()
    except Exception as exc:
        logger.warning("narrative generation failed: %s", exc)
        return ""


_COA_SYSTEM = """You are a US national security and economic warfare policy advisor. Given an intelligence analysis and the analyst's specific question, produce 3-4 concise, actionable Courses of Action (CoAs) that US or allied governments could take.

Rules:
- If the analyst asked a specific question, tailor your recommendations to directly address their concern
- Each CoA must reference specific data from the analysis (entity names, countries, programs, trade routes)
- Frame actions in terms of policy levers: sanctions designations, export controls, diplomatic engagement, intelligence collection, financial monitoring, trade restrictions, allied coordination
- Be specific: name the agency, regulation, or mechanism
- Each CoA should be 1-2 sentences
- State recommendations with full confidence — do NOT hedge with phrases like "consider", "may want to", "could potentially", or "warrants further review". Use directive language: "Designate...", "Direct FinCEN to...", "Coordinate with allies to..."
- Return ONLY a JSON array of strings, no markdown

CRITICAL — cite the correct legal mechanism for each situation:
- Forced labor (China/Xinjiang): UFLPA, CBP UFLPA Entity List
- Forced labor (non-China): Section 307 of Tariff Act, CBP Withhold Release Orders (WROs), ILAB List
- Iran sanctions: CAATSA, EO 13846, OFAC Iran programs (IRAN-EO13902, IFSR, etc.)
- Russia sanctions: EO 14024, OFAC Russia/Ukraine programs, EU Council Regulations
- Export controls: BIS Entity List, Military End-User List, Denied Persons List
- Trade retaliation: Section 301 (tariffs), Section 232 (national security)
- Financial monitoring: FinCEN, BSA/AML, correspondent banking restrictions
Do NOT conflate these — e.g. do not cite UFLPA for Vietnamese goods or BIS for sanctions."""


async def generate_recommendations(data_summary: dict, analyst_question: str = "") -> list[str]:
    """Generate 3-4 actionable CoAs from analysis data. Returns [] on failure.

    Routed through the pluggable text provider (issue #33).
    """
    from src.common.llm_provider import get_text_provider

    provider = get_text_provider()
    if not provider:
        return []
    try:
        user_content = ""
        if analyst_question:
            user_content = f"ANALYST QUESTION: {analyst_question}\n\nINTELLIGENCE DATA:\n"
        user_content += json.dumps(data_summary)

        text = await asyncio.wait_for(
            provider.complete(
                system=_COA_SYSTEM,
                messages=[{"role": "user", "content": user_content}],
                max_tokens=600,
            ),
            timeout=15.0,
        )
        text = (text or "").strip()
        # Parse JSON array from response
        if text.startswith("["):
            return json.loads(text)
        # Try extracting JSON from markdown fence
        match = re.search(r"\[.*\]", text, re.DOTALL)
        if match:
            return json.loads(match.group())
        return []
    except Exception as exc:
        logger.warning("recommendations generation failed: %s", exc)
        return []
