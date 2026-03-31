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
- "company"     = a single publicly traded corp, private company, state-owned enterprise, or org
- "person"      = a named individual — oligarch, official, executive, sanctioned person, minister
- "sector"      = an industry sector or commodity group (semiconductors, energy, rare earths, shipping, pharma)
- "vessel"      = a ship, tanker, or cargo vessel — identified by name, IMO number (7 digits), or MMSI (9 digits)
- "orchestrator"= a complex analytical question involving multiple entities, relationships, hypotheticals, \
or supply-chain/geopolitical analysis that cannot be answered by looking up a single entity

Use "orchestrator" only when the query genuinely requires cross-domain reasoning across multiple entities \
(e.g. "What is the relationship between Gazprom and Shell?", "Map the supply chain exposure of the EU \
semiconductor sector"). Do NOT use "orchestrator" for single-entity questions framed as hypotheticals \
(e.g. "What if we sanction TSMC?" → this is a company query about TSMC).

Extract the canonical entity name or identifier from the query. For vessels, return only the vessel \
name or numeric identifier — strip command words like "track"/"find"/"show me", articles like "the"/"a", \
and type words like "vessel"/"ship"/"tanker". For orchestrator queries, set entity_name to the full query.

Default to "company" if the entity is ambiguous.

Query: {query}

Respond with JSON only, no markdown fences:
{{"entity_type": "company|person|sector|vessel|orchestrator", "entity_name": "...", "confidence": 0.0, "reasoning": "one sentence"}}"""


@dataclass
class EntityResolution:
    entity_type: str   # "company" | "person" | "sector" | "vessel"
    entity_name: str   # extracted canonical name or identifier
    confidence: float  # 0.0 – 1.0
    reasoning: str


async def resolve_entity_type(query: str) -> EntityResolution:
    """Classify the query entity type and extract the canonical name using Claude."""
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
        if entity_type not in ("company", "person", "sector", "vessel", "orchestrator"):
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
