"""Extract durable, reusable facts from a completed analysis (Phase 5).

Runs as a BACKGROUND task after the assessment is already stored — never in the
analyst's critical path, and never able to fail an analysis. One cheap Haiku call
over the summary + findings + entity graph (not the raw tool JSON), then a hard
provenance filter.

The top risk this guards against is **memory poisoning**: an LLM-extracted "fact"
fed back into future plans is a hallucination that compounds — one wrong Monday
extraction silently steers every later session. The defenses, all mandatory and
all here rather than "later":

  1. Drop any fact whose cited sources don't intersect the assessment's REAL
     sources. If the model can't ground it in a source the analysis actually
     used, it doesn't get stored. This kills fabricated provenance.
  2. Everything is stored as UNVERIFIED PRIOR WORK (the recall injector labels it
     so, and never lets it become an assessment's cited source).
  3. Exact-hash + semantic dedup so a fact reinforces rather than duplicates.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from src.common import agent_memory
from src.common.agent_memory import Memory
from src.common.config import config
from src.llm import get_anthropic_client

logger = logging.getLogger(__name__)

# The extraction model. Haiku by design: this is a structured, cheap pass, and it
# runs in the background so latency doesn't matter to the analyst.
_EXTRACT_MODEL = config.decompose_model
_MAX_ASSESSMENT_CHARS = 12000  # summary + findings + graph, not raw tool output

MEMORY_EXTRACTION_PROMPT = """\
You are curating an intelligence analyst's long-term memory. Below is a completed
analysis. Extract ONLY durable, reusable facts — things that will still be true
and still be useful in three months.

EXTRACT:
- Entity facts: ownership, control, sanctions / export-control status, jurisdiction,
  identifiers (LEI/ticker/IMO), sector role. One fact per sentence.
- Relationship facts: X supplies Y; X owns Z; X and Y share a UBO; X routes through Z.
- Exposure facts: which allied / Western entities are exposed to X, and via what channel.
- Analyst interests: what this analyst is evidently tracking (sectors, countries,
  scenario patterns). Mark these memory_type "preference".

DO NOT EXTRACT:
- Anything not supported by a NAMED SOURCE in the analysis. If you cannot name the
  source, drop the fact.
- Prices, market caps, headlines, or anything that decays within weeks.
- Restatements of the analyst's question.
- Speculation ("could", "may", "is likely to") — those are judgments, not memories.
  (A stress-test caveat may be recorded as a "fact" with confidence LOW, phrased as
  the caveat, e.g. "Ownership of X is unconfirmed below the second tier.")

ANALYST QUESTION: {query}

AVAILABLE SOURCES (you may ONLY cite names from this list):
{sources}

ANALYSIS:
{assessment}

Return ONLY a JSON array. Each item:
{{
  "text": "one self-contained sentence; name the entities explicitly, no pronouns",
  "memory_type": "fact" | "preference" | "summary",
  "topics": ["ownership"|"sanctions"|"supply_chain"|"market"|"maritime"|"geopolitics"|"analyst_interest"],
  "entity_names": ["exact names as they appear above"],
  "confidence": "HIGH" | "MEDIUM" | "LOW",
  "sources": ["source names EXACTLY as listed above"]
}}

At most 12 facts, 2 preferences, 1 summary. Fewer is better than padded.
Return [] if nothing is worth remembering.
"""


def _assessment_digest(assessment: dict[str, Any]) -> str:
    """Compact, extraction-ready view: summary + findings + entities (not tool JSON)."""
    parts: list[str] = []
    if assessment.get("executive_summary"):
        parts.append("SUMMARY:\n" + str(assessment["executive_summary"]))
    findings = assessment.get("findings") or []
    if findings:
        lines = [
            f"- [{f.get('confidence', '?')}] {f.get('category', '')}: {f.get('finding', '')}"
            for f in findings
            if isinstance(f, dict)
        ]
        parts.append("FINDINGS:\n" + "\n".join(lines))
    ff = assessment.get("friendly_fire") or []
    if ff:
        lines = [
            f"- {x.get('entity')}: {x.get('exposure_type') or x.get('details') or ''}"
            for x in ff
            if isinstance(x, dict)
        ]
        parts.append("FRIENDLY FIRE:\n" + "\n".join(lines))
    entities = (assessment.get("entity_graph") or {}).get("entities") or []
    if entities:
        lines = [
            f"- {e.get('name')} ({e.get('entity_type') or '?'}, {e.get('country') or '?'})"
            for e in entities
            if isinstance(e, dict) and e.get("name")
        ]
        parts.append("ENTITIES:\n" + "\n".join(lines))
    return "\n\n".join(parts)[:_MAX_ASSESSMENT_CHARS]


def _real_source_names(assessment: dict[str, Any]) -> set[str]:
    """Lowercased names of sources the analysis ACTUALLY used."""
    names: set[str] = set()
    for s in assessment.get("sources") or []:
        if isinstance(s, dict) and s.get("name"):
            names.add(s["name"].strip().lower())
        elif isinstance(s, str) and s.strip():
            names.add(s.strip().lower())
    return names


def _known_entities(assessment: dict[str, Any]) -> list[dict]:
    return agent_memory.entities_from_assessment(assessment)


async def extract_memories(
    *,
    query: str,
    assessment: dict[str, Any],
    user_id: str,
    session_id: str | None,
    analysis_id: str,
) -> list[Memory]:
    """Ask Haiku for facts, then keep only the provenance-grounded ones.

    Returns the Memory objects that survived the source filter (already resolved
    to entity_ids where possible). Empty on any failure — extraction never raises.
    """
    client = get_anthropic_client()
    if client is None:
        return []

    real_sources = _real_source_names(assessment)
    if not real_sources:
        # No grounded sources in the assessment => nothing we could safely keep.
        return []

    digest = _assessment_digest(assessment)
    if not digest.strip():
        return []

    prompt = MEMORY_EXTRACTION_PROMPT.format(
        query=query,
        sources="\n".join(f"- {s}" for s in sorted(real_sources)),
        assessment=digest,
    )

    try:
        resp = await client.messages.create(
            model=_EXTRACT_MODEL,
            max_tokens=2000,
            system="You extract durable intelligence facts as strict JSON. No prose.",
            messages=[{"role": "user", "content": prompt}],
        )
        raw = resp.content[0].text
    except Exception as exc:  # noqa: BLE001
        logger.warning("Memory extraction LLM call failed for %s: %s", analysis_id, exc)
        return []

    items = _parse_items(raw)
    if not items:
        return []

    # Resolve entity names to graph ids so recall can filter by id later.
    name_to_id = {
        (e.get("name") or "").strip().lower(): e.get("entity_id")
        for e in _known_entities(assessment)
        if e.get("entity_id")
    }

    kept: list[Memory] = []
    dropped_unsourced = 0
    for item in items:
        if not isinstance(item, dict):
            continue
        text = str(item.get("text") or "").strip()
        if not text:
            continue

        # THE poisoning guard: a fact must cite a source the analysis really used.
        cited = {str(s).strip().lower() for s in (item.get("sources") or []) if str(s).strip()}
        grounded = cited & real_sources
        item_type = item.get("memory_type", "fact")
        if not grounded and item_type != "preference":
            # Preferences are about the analyst, not a sourced claim — exempt.
            dropped_unsourced += 1
            continue

        names = [str(n) for n in (item.get("entity_names") or []) if str(n).strip()]
        kept.append(
            Memory(
                text=text,
                memory_type=item_type,
                topics=[str(t) for t in (item.get("topics") or [])],
                entity_names=names,
                entity_ids=[name_to_id[n.lower()] for n in names if n.lower() in name_to_id],
                confidence=str(item.get("confidence") or "MEDIUM"),
                sources=[{"name": s} for s in sorted(grounded)]
                or [{"name": s} for s in sorted(cited)],
                source_analysis_id=analysis_id,
                source_session_id=session_id,
            )
        )

    if dropped_unsourced:
        logger.info(
            "Memory extraction for %s dropped %d unsourced fact(s); kept %d.",
            analysis_id,
            dropped_unsourced,
            len(kept),
        )
    return kept


async def extract_and_remember(
    *,
    query: str,
    assessment: dict[str, Any],
    user_id: str,
    session_id: str | None,
    analysis_id: str,
) -> int:
    """The background entry point: extract, semantic-dedup, persist. Never raises.

    Returns the number of NEW memories written (reinforced duplicates aren't new).
    """
    try:
        candidates = await extract_memories(
            query=query,
            assessment=assessment,
            user_id=user_id,
            session_id=session_id,
            analysis_id=analysis_id,
        )
        if not candidates:
            return 0

        # Semantic dedup before insert: a near-restatement reinforces the existing
        # memory instead of adding a duplicate. Exact-hash dedup in remember() is
        # the backstop when the vector index isn't available.
        fresh: list[Memory] = []
        for m in candidates:
            if await agent_memory.maybe_merge_semantic_duplicate(user_id, m):
                continue
            fresh.append(m)

        return agent_memory.remember(user_id, fresh)
    except Exception as exc:  # noqa: BLE001 — memory must never break an analysis
        logger.warning("extract_and_remember failed for %s: %s", analysis_id, exc)
        return 0


def _parse_items(raw: str) -> list[Any]:
    """Pull the JSON array out of the model's reply, tolerant of code fences.

    Deliberately NOT main._extract_json: that one hunts for the first ``{...}``
    object, so on a top-level ``[{...}, {...}]`` array it returns only the first
    element. Extraction returns an array, so parse the array directly.
    """
    text = (raw or "").strip()

    # Strip a ```json / ``` fence if present.
    if text.startswith("```"):
        parts = text.split("```")
        if len(parts) >= 2:
            text = parts[1]
        if text.startswith("json"):
            text = text[4:]
        text = text.strip()

    for candidate in (text, _outermost_array(text)):
        if not candidate:
            continue
        try:
            parsed = json.loads(candidate)
        except Exception:  # noqa: BLE001
            continue
        if isinstance(parsed, list):
            return parsed
        if isinstance(parsed, dict):
            for key in ("memories", "facts", "items"):
                if isinstance(parsed.get(key), list):
                    return parsed[key]
    return []


def _outermost_array(text: str) -> str | None:
    start = text.find("[")
    end = text.rfind("]")
    if start != -1 and end > start:
        return text[start : end + 1]
    return None
