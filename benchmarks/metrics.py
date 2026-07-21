"""Scoring for the benchmark — objective, deterministic metrics.

Two families:
  * **Structural richness** — countable signals off an ImpactAssessment
    (findings, sources, entities, relationships, recommendations, …). A cheap,
    reproducible proxy for "how substantive is the answer"; complements the
    LLM judge (judge.py), which scores quality.
  * **Task quality** — scored against the golden labels in corpus.py: entity
    ranking correctness, suggest hit-rate + safety, memory recall@k.
"""

from __future__ import annotations

from statistics import median
from typing import Any


# --- structural richness -----------------------------------------------------


def structural_richness(assessment: dict[str, Any] | None) -> dict[str, Any]:
    """Countable substance signals off an ImpactAssessment (see types.py)."""
    if not assessment:
        return {"present": False}
    graph = assessment.get("entity_graph") or {}
    conf = assessment.get("confidence_summary") or {}
    conf_dist: dict[str, int] = {}
    for v in conf.values():
        conf_dist[str(v)] = conf_dist.get(str(v), 0) + 1
    return {
        "present": True,
        "executive_summary_chars": len((assessment.get("executive_summary") or "")),
        "findings": len(assessment.get("findings") or []),
        "sources": len(assessment.get("sources") or []),
        "entities": len(graph.get("entities") or []),
        "relationships": len(graph.get("relationships") or []),
        "recommendations": len(assessment.get("recommendations") or []),
        "friendly_fire": len(assessment.get("friendly_fire") or []),
        "confidence_distribution": conf_dist,
    }


def assessment_entity_names(assessment: dict[str, Any] | None) -> set[str]:
    """Lowercased entity names referenced anywhere in an assessment."""
    if not assessment:
        return set()
    names: set[str] = set()
    for e in (assessment.get("entity_graph") or {}).get("entities") or []:
        if isinstance(e, dict) and e.get("name"):
            names.add(e["name"].strip().lower())
    for t in (assessment.get("query") or {}).get("target_entities") or []:
        if isinstance(t, str):
            names.add(t.strip().lower())
    return names


# --- S1: entity ranking ------------------------------------------------------


def score_entity_ranking(results: list[dict[str, Any]], golden: dict[str, Any]) -> dict[str, Any]:
    """Did the ranking put the genuine peer above the token-collision decoy?"""
    order = [r.get("entity", {}).get("entity_id") for r in results]
    pos = {eid: i for i, eid in enumerate(order)}

    inversions_ok = 0
    inversions_total = 0
    detail = []
    for a, b in golden["expect_outranks"]:
        inversions_total += 1
        # If either is missing it's not "above"; require both present + a<b.
        ok = a in pos and b in pos and pos[a] < pos[b]
        inversions_ok += 1 if ok else 0
        detail.append({"expect": f"{a} > {b}", "ok": ok, "pos_a": pos.get(a), "pos_b": pos.get(b)})

    top_k = set(order[: len(golden["relevant_ids"]) + 1])
    relevant_in_top = len(golden["relevant_ids"] & top_k)
    decoy_at_top = bool(order) and order[0] in golden["decoy_ids"]

    return {
        "order": order,
        "ranking_correct": inversions_ok == inversions_total,
        "inversions_ok": inversions_ok,
        "inversions_total": inversions_total,
        "relevant_in_top": relevant_in_top,
        "relevant_total": len(golden["relevant_ids"]),
        "decoy_ranked_first": decoy_at_top,
        "detail": detail,
    }


# --- S2: suggest -------------------------------------------------------------


def _norm(s: str | None) -> str:
    return " ".join((s or "").lower().split())


def score_suggest(resp: dict[str, Any], case: dict[str, Any]) -> dict[str, Any]:
    """Hit-rate for paraphrases; SAFETY for entity swaps (must not be offered)."""
    suggestion = resp.get("suggestion")
    backend = resp.get("backend", "lexical")
    out: dict[str, Any] = {
        "label": case["label"],
        "suggestion": suggestion,
        "score": resp.get("score", 0.0),
        "backend": backend,
    }

    if case["label"] == "entity_swap":
        # SAFETY: the reworded-but-different-company query must NOT be offered the
        # other company's warmed answer.
        offered_wrong = _norm(suggestion) == _norm(case["must_not_map_to"])
        out["safe"] = not offered_wrong
        out["pass"] = not offered_wrong
    elif case.get("expect_maps_to") is None:
        out["pass"] = suggestion is None  # unrelated → no suggestion
    else:
        hit = _norm(suggestion) == _norm(case["expect_maps_to"])
        out["hit"] = hit
        out["pass"] = hit
    return out


# --- S5: memory recall -------------------------------------------------------


def score_memory_recall(
    recalled_names: set[str], friday_assessment: dict[str, Any] | None, scenario: dict[str, Any]
) -> dict[str, Any]:
    """Did Friday's run recall Monday's supply-chain entities (and not the decoy)?

    `recalled_names` = names surfaced by /api/memory/search (the recall API); the
    Friday assessment is checked too, since a working recall should make those
    entities appear in the follow-on answer.
    """
    want = {n.lower() for n in scenario["expect_recalled_entities"]}
    exclude = {n.lower() for n in scenario["expect_excluded_entities"]}
    answer_names = assessment_entity_names(friday_assessment)
    surfaced = recalled_names | answer_names

    recalled = want & surfaced
    leaked_decoy = exclude & recalled_names  # decoy dominating recall is bad
    return {
        "recall_at_k": len(recalled) / len(want) if want else 0.0,
        "recalled": sorted(recalled),
        "missed": sorted(want - surfaced),
        "decoy_leaked": sorted(leaked_decoy),
        "any_recall": bool(recalled),
    }


# --- latency -----------------------------------------------------------------


def latency_stats(seconds_list: list[float]) -> dict[str, float]:
    if not seconds_list:
        return {"n": 0}
    s = sorted(seconds_list)
    return {
        "n": len(s),
        "min": round(s[0], 3),
        "p50": round(median(s), 3),
        "p95": round(s[min(len(s) - 1, int(0.95 * (len(s) - 1)))], 3),
        "max": round(s[-1], 3),
    }
