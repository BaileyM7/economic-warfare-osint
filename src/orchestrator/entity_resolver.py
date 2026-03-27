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


async def resolve_entity_type(query: str) -> EntityResolution:
    """Use Claude to classify the entity type and extract the entity name."""
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
