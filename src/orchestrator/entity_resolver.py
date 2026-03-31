"""Entity type resolution — classifies a query subject into the ontology:
   company | person | sector | vessel

This runs before tool selection so the orchestrator can route to
entity-appropriate data sources and renderers.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

import anthropic

from src.common.config import config

_CLASSIFIER_PROMPT = """\
You are an entity classifier for an economic warfare OSINT system.

Given a user query, identify the PRIMARY entity being asked about and classify it.

Entity types:
- "company"  = publicly traded corp, private company, state-owned enterprise, conglomerate, org
- "person"   = named individual — oligarch, official, executive, sanctioned person, general, minister
- "sector"   = industry sector or commodity group (semiconductors, energy, rare earths, shipping, pharma)
- "vessel"   = ship, tanker, cargo vessel — vessel names, IMO numbers (9-digit), MMSI (9-digit)

Also extract the canonical entity name or identifier from the query.

Default to "company" if the entity is ambiguous.

Query: {query}

Respond with JSON only, no markdown fences:
{{"entity_type": "company|person|sector|vessel", "entity_name": "...", "confidence": 0.0, "reasoning": "one sentence"}}"""


@dataclass
class EntityResolution:
    entity_type: str   # "company" | "person" | "sector" | "vessel"
    entity_name: str   # extracted canonical name or identifier
    confidence: float  # 0.0 – 1.0
    reasoning: str


import re

# Fast pattern matching — skips Claude for obvious cases
_VESSEL_KEYWORDS = re.compile(
    r"\b(track vessel|vessel|ship|tanker|cargo|bulk carrier|container ship|"
    r"IMO\s*\d{7}|MMSI\s*\d{9}|MV |MT |MY )\b", re.IGNORECASE
)
_PERSON_KEYWORDS = re.compile(
    r"\b(oligarch|sanctioned person|minister|general|admiral|president|"
    r"who is|profile of|insider threat)\b", re.IGNORECASE
)
_SECTOR_KEYWORDS = re.compile(
    r"\b(sector|industry|semiconductor|energy sector|rare earth|"
    r"shipping industry|defense sector|pharma sector)\b", re.IGNORECASE
)


def _fast_resolve(query: str) -> EntityResolution | None:
    """Tier 0: instant pattern-based resolution for obvious queries."""
    q = query.strip()

    if _VESSEL_KEYWORDS.search(q):
        # Strip the keyword prefix to get the vessel name
        name = re.sub(
            r"^(track\s+vessel|track|vessel)\s+", "", q, flags=re.IGNORECASE
        ).strip() or q
        return EntityResolution("vessel", name, 0.95, "Vessel keyword detected")

    if _PERSON_KEYWORDS.search(q):
        name = re.sub(
            r"^(who is|profile of)\s+", "", q, flags=re.IGNORECASE
        ).strip() or q
        return EntityResolution("person", name, 0.90, "Person keyword detected")

    if _SECTOR_KEYWORDS.search(q):
        return EntityResolution("sector", q, 0.90, "Sector keyword detected")

    # Check for IMO/MMSI numeric patterns
    imo_match = re.search(r"\bIMO\s*(\d{7})\b", q, re.IGNORECASE)
    if imo_match:
        return EntityResolution("vessel", imo_match.group(0), 0.95, "IMO number detected")
    mmsi_match = re.match(r"^\d{9}$", q.strip())
    if mmsi_match:
        return EntityResolution("vessel", q.strip(), 0.95, "MMSI number detected")

    return None


async def resolve_entity_type(query: str) -> EntityResolution:
    """Resolve entity type: fast pattern match first, then Claude for ambiguous cases."""
    # Tier 0: instant pattern match
    fast = _fast_resolve(query)
    if fast:
        return fast

    # Tier 1: Claude classification
    client = anthropic.AsyncAnthropic(api_key=config.anthropic_api_key)

    response = await client.messages.create(
        model=config.model,
        max_tokens=256,
        messages=[{"role": "user", "content": _CLASSIFIER_PROMPT.format(query=query)}],
    )

    text = response.content[0].text.strip()

    # Strip markdown fences if present
    if "```" in text:
        start = text.index("```") + 3
        if text[start : start + 4] == "json":
            start += 4
        end = text.rindex("```")
        text = text[start:end].strip()

    try:
        data = json.loads(text)
        entity_type = data.get("entity_type", "company")
        if entity_type not in ("company", "person", "sector", "vessel"):
            entity_type = "company"
        return EntityResolution(
            entity_type=entity_type,
            entity_name=data.get("entity_name", query),
            confidence=float(data.get("confidence", 0.7)),
            reasoning=data.get("reasoning", ""),
        )
    except (json.JSONDecodeError, KeyError, ValueError):
        return EntityResolution(
            entity_type="company",
            entity_name=query,
            confidence=0.5,
            reasoning="Fallback: classification parsing failed",
        )
