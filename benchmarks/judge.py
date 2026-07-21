"""LLM-as-judge for answer richness/quality.

Two scorers, both using Claude (config.model). The judge runs LOCALLY and is
independent of the target site — it just reads the two answers.

  * `rubric(query, assessment)` → 1-5 scores on specificity, entity coverage,
    actionability, groundedness, plus an overall.
  * `pairwise(query, before, after)` → which answer is richer. Position bias is
    cancelled by running BOTH orderings and only declaring a winner when the two
    runs agree; otherwise it's a tie.

No key / any failure → returns None, and the caller simply omits the judge
column. The judge never blocks a run.
"""

from __future__ import annotations

import json
from typing import Any

from src.common.config import config
from src.llm import get_anthropic_client


def answer_text(assessment: dict[str, Any] | None) -> str:
    """Flatten an ImpactAssessment into the prose a judge should read."""
    if not assessment:
        return "(no answer produced)"
    parts = [assessment.get("executive_summary") or ""]
    for f in assessment.get("findings") or []:
        if isinstance(f, dict):
            parts.append(
                f"- [{f.get('confidence', '?')}] {f.get('category', '')}: {f.get('finding', '')}"
            )
    recs = assessment.get("recommendations") or []
    if recs:
        parts.append("Recommendations: " + "; ".join(str(r) for r in recs))
    return "\n".join(p for p in parts if p).strip()[:8000]


def _client():
    return get_anthropic_client()


def _ask_json(client, prompt: str, max_tokens: int = 600) -> dict[str, Any] | None:
    try:
        resp = client.messages.create(
            model=config.model,
            max_tokens=max_tokens,
            system="You are a meticulous intelligence-analysis reviewer. Reply with STRICT JSON only, no prose.",
            messages=[{"role": "user", "content": prompt}],
        )
        raw = resp.content[0].text
        start, end = raw.find("{"), raw.rfind("}")
        if start == -1 or end <= start:
            return None
        return json.loads(raw[start : end + 1])
    except Exception:  # noqa: BLE001
        return None


_RUBRIC_PROMPT = """\
Score this economic-warfare intelligence answer to the analyst's question. Judge only what is
written — do not reward length for its own sake.

QUESTION: {query}

ANSWER:
{answer}

Return JSON with integer 1-5 scores (1=poor, 5=excellent) and one-line reasons:
{{
  "specificity": <1-5>,        // named entities/figures/mechanisms vs vague generalities
  "entity_coverage": <1-5>,    // breadth+relevance of companies/people/programs identified
  "actionability": <1-5>,      // concrete, decision-useful recommendations tied to findings
  "groundedness": <1-5>,       // claims supported by cited sources/data, not speculation
  "overall": <1-5>,
  "reason": "<one sentence>"
}}
"""


def rubric(query: str, assessment: dict[str, Any] | None) -> dict[str, Any] | None:
    client = _client()
    if client is None:
        return None
    return _ask_json(client, _RUBRIC_PROMPT.format(query=query, answer=answer_text(assessment)))


_PAIRWISE_PROMPT = """\
Two answers to the same analyst question. Decide which is the RICHER, more useful intelligence
answer — more specific, better entity coverage, better grounded, more actionable.

QUESTION: {query}

ANSWER A:
{a}

ANSWER B:
{b}

Return JSON: {{"winner": "A" | "B" | "tie", "reason": "<one sentence>"}}
"""


def _one_pairwise(client, query: str, a: str, b: str) -> str | None:
    out = _ask_json(client, _PAIRWISE_PROMPT.format(query=query, a=a, b=b), max_tokens=200)
    if not out:
        return None
    w = str(out.get("winner", "")).strip().upper()
    return w if w in ("A", "B", "TIE") else None


def pairwise(
    query: str, before: dict[str, Any] | None, after: dict[str, Any] | None
) -> dict[str, Any] | None:
    return pairwise_text(query, answer_text(before), answer_text(after))


def pairwise_text(query: str, before_text: str, after_text: str) -> dict[str, Any] | None:
    """Blind, order-swapped: 'before' and 'after' each play A once. A winner is
    only declared when both orderings agree; otherwise 'tie' (bias-cancelled)."""
    client = _client()
    if client is None:
        return None
    bt, at = before_text, after_text

    # Round 1: before=A, after=B
    r1 = _one_pairwise(client, query, bt, at)
    # Round 2: swapped — after=A, before=B
    r2 = _one_pairwise(client, query, at, bt)
    if r1 is None or r2 is None:
        return None

    # Map each round's A/B verdict back to before/after.
    v1 = {"A": "before", "B": "after", "TIE": "tie"}[r1]
    v2 = {"A": "after", "B": "before", "TIE": "tie"}[r2]

    if v1 == v2 and v1 != "tie":
        winner = v1
    elif v1 == "tie" and v2 == "tie":
        winner = "tie"
    else:
        winner = "tie"  # disagreement across orderings → position bias → tie
    return {"winner": winner, "round1": v1, "round2": v2}
