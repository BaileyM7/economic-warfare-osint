"""Run the benchmark battery against ONE Emissary state and write a labeled,
health-stamped result set.

    # cheap scenarios only (suggest + entity similarity):
    python -m benchmarks.run --base-url http://localhost:8000 --label before \
        --user analyst --password demo --scenarios s1,s2 --out benchmarks/results/before.json

    # everything, incl. full cold analyses + the memory scenario (slow, $$):
    python -m benchmarks.run --base-url https://…onrender.com --label after \
        --user demo --password demo123 --scenarios s1,s2,s3,s4,s5

The output is state-agnostic and stamped with GET /api/health, so compare.py can
prove it's diffing a real BEFORE against a real AFTER (not two of the same).
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from benchmarks import corpus, metrics
from benchmarks.client import EmissaryClient

_RESULTS = Path(__file__).parent / "results"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# --- scenarios ---------------------------------------------------------------


def s1_entity_similarity(c: EmissaryClient, use_judge: bool) -> dict:
    g = corpus.ENTITY_SIMILARITY
    # Seed the labeled entities (cleaned up after), then ask for neighbours using
    # the server's DEPLOYED default backend — so the result reflects BEFORE/AFTER.
    for eid, name, etype, country, notes in g["seed_entities"]:
        c.upsert_entity(eid, name, etype, country, notes)
    # The vector write-through is async (fire-and-forget in the server), so give
    # the reindex a moment to land before querying — otherwise a fresh index is
    # still empty and the search falls back to lexical. On AFTER with a warm
    # EmbeddingsCache this is ms of work; the settle is generous.
    time.sleep(3)
    try:
        timed = c.entity_similar(entity_id=g["target_id"], top_k=5)
        resp = timed.value
        score = metrics.score_entity_ranking(resp.get("results") or [], g)
        return {
            "backend_used": resp.get("backend_used"),
            "note": resp.get("note"),
            "latency_s": round(timed.seconds, 3),
            "score": score,
            "results": [
                {
                    "name": r.get("entity", {}).get("name"),
                    "score": r.get("score"),
                    "why": (r.get("basis") or {}).get("why"),
                }
                for r in (resp.get("results") or [])
            ],
        }
    finally:
        for eid, *_ in g["seed_entities"]:
            c.delete_entity(eid)


def s2_suggest(c: EmissaryClient) -> dict:
    cases = []
    latencies = []
    for case in corpus.SUGGEST_CASES:
        timed = c.suggest(case["query"])
        latencies.append(timed.seconds)
        scored = metrics.score_suggest(timed.value, case)
        scored["query"] = case["query"]
        scored["latency_s"] = round(timed.seconds, 3)
        cases.append(scored)
    passed = sum(1 for x in cases if x.get("pass"))
    return {
        "cases": cases,
        "passed": passed,
        "total": len(cases),
        "backend": cases[0]["backend"] if cases else None,
        "latency": metrics.latency_stats(latencies),
    }


def s3_cold_analyze(c: EmissaryClient, use_judge: bool, force_fresh: bool) -> dict:
    from benchmarks import judge

    per_query = []
    latencies = []
    for q in corpus.DEMO_QUERIES:
        timed = c.analyze(q, force_fresh=force_fresh)
        latencies.append(timed.seconds)
        result = (timed.value or {}).get("result")
        entry = {
            "query": q,
            "status": timed.value.get("status"),
            "latency_s": round(timed.seconds, 2),
            "polls": timed.extra.get("polls"),
            "is_replay": timed.extra.get("is_replay"),
            "richness": metrics.structural_richness(result),
            # Compact answer text so compare.py can run the pairwise judge without
            # re-storing the full (huge) tool_results payload.
            "answer": judge.answer_text(result),
        }
        if use_judge:
            entry["judge_rubric"] = judge.rubric(q, result)
        per_query.append(entry)
    return {"per_query": per_query, "latency": metrics.latency_stats(latencies)}


def s4_replay(c: EmissaryClient) -> dict:
    # Assumes the query is already warmed (run s3 first, or a warmed demo box).
    timed = c.analyze(corpus.REPLAY_QUERY)
    return {
        "query": corpus.REPLAY_QUERY,
        "latency_s": round(timed.seconds, 2),
        "is_replay": timed.extra.get("is_replay"),
        "status": timed.value.get("status"),
    }


def s5_memory(c: EmissaryClient, use_judge: bool, extraction_wait: float) -> dict:
    from benchmarks import judge

    sc = corpus.MEMORY_SCENARIO
    # MONDAY: run the seed analysis under a fresh session.
    monday = c.analyze(sc["monday_query"])
    session_id = monday.extra.get("session_id")
    out: dict = {"session_id": session_id, "monday_latency_s": round(monday.seconds, 2)}
    if not session_id:
        out["error"] = "no session_id returned (feature off / old code) — memory before/after N/A"
        return out

    # Give background extraction time to land (Haiku pass, non-blocking).
    time.sleep(extraction_wait)

    # What does recall surface for the anaphoric Friday query?
    rec = c.memory_search(sc["friday_query"])
    recalled_names: set[str] = set()
    if not rec.extra.get("absent") and rec.value:
        for item in rec.value.get("results") or []:
            for n in (item.get("memory") or {}).get("entity_names") or []:
                recalled_names.add(str(n).lower())

    # FRIDAY: the anaphoric follow-on in the SAME session.
    friday = c.analyze(sc["friday_query"], session_id=session_id)
    friday_result = (friday.value or {}).get("result")

    out.update(
        {
            "friday_latency_s": round(friday.seconds, 2),
            "recall_backend": (rec.value or {}).get("backend") if rec.value else "absent",
            "recall_score": metrics.score_memory_recall(recalled_names, friday_result, sc),
            "friday_richness": metrics.structural_richness(friday_result),
            "friday_answer": judge.answer_text(friday_result),
        }
    )
    if use_judge:
        out["friday_judge_rubric"] = judge.rubric(sc["friday_query"], friday_result)
    return out


# --- driver ------------------------------------------------------------------


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Run the Emissary before/after benchmark for one state."
    )
    ap.add_argument("--base-url", required=True)
    ap.add_argument("--label", required=True, help="e.g. before | after")
    ap.add_argument("--user", default="analyst")
    ap.add_argument("--password", default="demo")
    ap.add_argument("--scenarios", default="s1,s2", help="comma list of s1,s2,s3,s4,s5")
    ap.add_argument("--out", default=None)
    ap.add_argument("--no-judge", action="store_true", help="skip the LLM judge")
    ap.add_argument("--force-fresh", action="store_true", help="force cold analyze (skip replay)")
    ap.add_argument("--extraction-wait", type=float, default=12.0)
    args = ap.parse_args()

    wanted = {s.strip() for s in args.scenarios.split(",") if s.strip()}
    use_judge = not args.no_judge

    c = EmissaryClient(args.base_url, args.user, args.password)
    c.login()
    health = c.health()

    print(f"[{args.label}] {args.base_url}")
    print(
        f"  health oracle: redis.search={health.get('redis', {}).get('search')} "
        f"embeddings={health.get('embeddings', {}).get('enabled')} "
        f"entity_index={health.get('entity_index', {}).get('available')} "
        f"ltm={health.get('long_term_memory', {}).get('backend')} "
        f"analysis_store={health.get('analysis_store', {}).get('backend')}"
    )

    scenarios: dict = {}
    if "s1" in wanted:
        print("  S1 entity similarity…")
        scenarios["s1_entity_similarity"] = s1_entity_similarity(c, use_judge)
    if "s2" in wanted:
        print("  S2 semantic suggest…")
        scenarios["s2_suggest"] = s2_suggest(c)
    if "s3" in wanted:
        print("  S3 cold analyze (slow)…")
        scenarios["s3_cold_analyze"] = s3_cold_analyze(c, use_judge, args.force_fresh)
    if "s4" in wanted:
        print("  S4 replay latency…")
        scenarios["s4_replay"] = s4_replay(c)
    if "s5" in wanted:
        print("  S5 memory Monday→Friday (slow)…")
        scenarios["s5_memory"] = s5_memory(c, use_judge, args.extraction_wait)

    c.close()

    result = {
        "label": args.label,
        "base_url": args.base_url,
        "timestamp": _now(),
        "health": health,
        "scenarios": scenarios,
    }

    out_path = Path(args.out) if args.out else _RESULTS / f"{args.label}.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(result, indent=2))
    print(f"  wrote {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
