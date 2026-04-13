"""Entity type resolution — classifies a query subject into the ontology:
   company | person | sector | vessel

This runs before tool selection so the orchestrator can route to
entity-appropriate data sources and renderers.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from src.common.config import config
from src.llm import get_anthropic_client

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

Use "orchestrator" ONLY when the query genuinely requires cross-domain reasoning across MULTIPLE DISTINCT \
entities with no single primary subject (e.g. "What is the relationship between Gazprom and Shell?", \
"Map the supply chain exposure of the EU semiconductor sector").

IMPORTANT: If the query mentions a single named entity (vessel, person, company, sector) but adds \
context, hypotheticals, or asks "what should we do", classify by the PRIMARY entity type, NOT as \
orchestrator. Examples:
- "What should I do about the Ever Given, given current events in Iran?" → vessel (Ever Given)
- "Ever Given" → vessel (Ever Given) — well-known container ship that blocked the Suez Canal
- "LADY M" → vessel (LADY M) — sanctioned superyacht
- "What if we sanction TSMC?" → company (TSMC)
- "How should we respond to Viktor Vekselberg's sanctions evasion?" → person (Viktor Vekselberg)
- "What export controls should apply to semiconductors given China tensions?" → sector (semiconductor)
The system will use the full question text to generate context-aware recommendations.

Use your knowledge of well-known vessels, persons, companies, and sectors when classifying. \
Famous ship names (Ever Given, Lady M, Cosco Shipping, MSC vessels, dark fleet tankers, etc.) \
should be classified as "vessel" even without explicit "ship" or "vessel" keywords. Famous \
sanctioned individuals (oligarchs, ministers, sanctioned business figures) should be "person".

Extract the canonical entity name or identifier from the query. For vessels, return only the vessel \
name or numeric identifier — strip command words like "track"/"find"/"show me"/"what should I do about", \
articles like "the"/"a", and type words like "vessel"/"ship"/"tanker". For orchestrator queries, set \
entity_name to the full query.

Default to "company" only if the entity is genuinely ambiguous AND you have no knowledge of it as \
a vessel, person, or sector. Do NOT default to company for proper nouns you recognize as vessels.

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
    """Classify the query entity type and extract the canonical name using Claude.

    Uses temperature=0 for deterministic classification — we want the same
    query to always return the same type, not creative variation. Also uses
    the shared client so it inherits the SDK's retry-on-overload behavior.
    """
    client = get_anthropic_client()
    if client is None:
        return EntityResolution(
            entity_type="company",
            entity_name=query,
            confidence=0.0,
            reasoning="Anthropic API not configured",
        )

    response = await client.messages.create(
        model=config.model,
        max_tokens=256,
        temperature=0,  # deterministic classification
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
