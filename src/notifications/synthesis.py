"""LLM-generated opening paragraph for the weekly digest.

Uses Claude Haiku for cost (this runs weekly per opt-in user). On any
failure — API timeout, rate limit, missing key — falls back to a
deterministic template so the digest still goes out.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from anthropic import Anthropic

    from src.notifications.email_digest import WeekData

log = logging.getLogger(__name__)

MODEL = "claude-haiku-4-5-20251001"

PROMPT_TEMPLATE = """You are summarizing a weekly geopolitical/economic risk brief for a single reader.

Their watchlist saw {n_cards} updates this week ({n_high} at the highest urgency level).
Top entities mentioned: {top_entities}.
Standout event: {standout}

Write a 2-3 sentence opening paragraph that synthesizes the week from THIS reader's perspective.

Audience and tone:
- Write for an executive who follows business and geopolitics generally but is NOT an OSINT/sanctions specialist.
- Expand acronyms on first mention (e.g. "OFAC (the U.S. Treasury's sanctions office)", "OPEC+ (major oil producers plus Russia)").
- When you mention a company by ticker, also use the full name (e.g. "Sinopec" not just "SNP").
- Use plain English. Avoid insider phrases like "SDN designation," "secondary sanctions exposure," "feedstock disruption" — translate them.
- Replace "high-severity" with concrete framing: "the most urgent," "the one to read first," etc.

Be concrete, no fluff, no hedging. Refer to specific entities by name. Output only the paragraph — no greeting, no headers, no markdown."""

FALLBACK_TEMPLATE = (
    "This week your watchlist saw {n_cards} updates across the feed, "
    "{n_high} of them high-severity. Top of mind: {top_entities}."
)


def _summarize_inputs(week_data: WeekData) -> dict:
    """Extract the small numeric/string set passed to either the LLM or the fallback."""
    cards = week_data.top_cards
    n_cards = len(cards)
    n_high = sum(1 for c in cards if c.get("severity") in ("HIGH", "CRITICAL"))
    # Ordered dedupe so the entity list reflects severity ordering, not arbitrary set ordering.
    seen: set[str] = set()
    top_entities_list: list[str] = []
    for c in cards[:3]:
        e = c.get("entity")
        if e and e not in seen:
            seen.add(e)
            top_entities_list.append(e)
    top_entities = ", ".join(top_entities_list) or "(none)"
    standout = cards[0].get("entity") if cards else ""
    return {
        "n_cards": n_cards,
        "n_high": n_high,
        "top_entities": top_entities,
        "standout": standout,
    }


def generate_opening_synthesis(
    week_data: WeekData,
    anthropic_client: Anthropic | None,
) -> str:
    """Generate the digest's opening paragraph.

    If `anthropic_client` is None OR the LLM call fails for any reason,
    returns the deterministic fallback. Never raises.
    """
    inputs = _summarize_inputs(week_data)

    if anthropic_client is None:
        return FALLBACK_TEMPLATE.format(**inputs)

    try:
        resp = anthropic_client.messages.create(
            model=MODEL,
            max_tokens=200,
            messages=[{"role": "user", "content": PROMPT_TEMPLATE.format(**inputs)}],
        )
        # resp.content is a list of content blocks; first is typically TextBlock
        text = resp.content[0].text.strip() if resp.content else ""
        if not text:
            log.warning("LLM returned empty synthesis, falling back")
            return FALLBACK_TEMPLATE.format(**inputs)
        return text
    except Exception:
        log.exception("synthesis LLM call failed, using fallback")
        return FALLBACK_TEMPLATE.format(**inputs)
