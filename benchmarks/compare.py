"""Diff a BEFORE and an AFTER result set into a before/after markdown report.

    python -m benchmarks.compare benchmarks/results/before.json benchmarks/results/after.json \
        [--out benchmarks/results/report.md] [--no-judge]

Cross-checks the two runs' /api/health stamps so you can't accidentally compare
two "afters". Runs the blind pairwise LLM judge on the S3 analyze answers.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


def _load(p: str) -> dict:
    return json.loads(Path(p).read_text())


def _health_line(h: dict) -> str:
    return (
        f"redis.search={h.get('redis', {}).get('search')} · "
        f"embeddings={h.get('embeddings', {}).get('enabled')} · "
        f"entity_index={h.get('entity_index', {}).get('available')} · "
        f"ltm={h.get('long_term_memory', {}).get('backend')} · "
        f"analysis_store={h.get('analysis_store', {}).get('backend')} · "
        f"semantic_suggest={h.get('semantic_cache', {}).get('suggest_backend')}"
    )


def _fmt(v: Any) -> str:
    return "—" if v is None else str(v)


def _section_s1(before: dict, after: dict, out: list[str]) -> None:
    b = before.get("scenarios", {}).get("s1_entity_similarity")
    a = after.get("scenarios", {}).get("s1_entity_similarity")
    if not (b or a):
        return
    out.append("\n## S1 — Entity similarity (the customer's #1 ask)\n")
    out.append(
        "Target: *Fujian Jinhua* (a DRAM fab). A correct ranking puts **SMIC** (a foundry) "
        "above **Jinhua Group Holdings** (a real-estate firm that only shares the token 'jinhua').\n"
    )
    out.append("| | backend | ranking correct? | decoy ranked #1? | latency |")
    out.append("|---|---|---|---|---|")
    for label, x in (("BEFORE", b), ("AFTER", a)):
        if not x:
            continue
        s = x.get("score", {})
        out.append(
            f"| {label} | {_fmt(x.get('backend_used'))} | "
            f"{'✅' if s.get('ranking_correct') else '❌'} | "
            f"{'⚠️ yes' if s.get('decoy_ranked_first') else 'no'} | "
            f"{_fmt(x.get('latency_s'))}s |"
        )
    for label, x in (("BEFORE", b), ("AFTER", a)):
        if x and x.get("results"):
            order = " > ".join(r["name"] for r in x["results"][:3])
            out.append(f"\n_{label} order:_ {order}")


def _section_s2(before: dict, after: dict, out: list[str]) -> None:
    b = before.get("scenarios", {}).get("s2_suggest")
    a = after.get("scenarios", {}).get("s2_suggest")
    if not (b or a):
        return
    out.append("\n\n## S2 — Semantic suggest ('Did you mean?') + safety\n")
    out.append(
        "Paraphrases *should* map to their warmed demo query; entity swaps (different company) "
        "must **not** be offered another company's answer.\n"
    )
    out.append("| | backend | passed | latency p50 |")
    out.append("|---|---|---|---|")
    for label, x in (("BEFORE", b), ("AFTER", a)):
        if not x:
            continue
        out.append(
            f"| {label} | {_fmt(x.get('backend'))} | {x.get('passed')}/{x.get('total')} | "
            f"{_fmt(x.get('latency', {}).get('p50'))}s |"
        )
    # Per-case detail (paraphrase hits + swap safety) from AFTER if present.
    x = a or b
    if x:
        out.append("\n| case | query | suggestion | verdict |")
        out.append("|---|---|---|---|")
        for cs in x.get("cases", []):
            verdict = "✅ pass" if cs.get("pass") else "❌ FAIL"
            if cs["label"] == "entity_swap":
                verdict += " (safe)" if cs.get("safe") else " (LEAKED)"
            out.append(
                f"| {cs['label']} | {cs['query'][:44]}… | {_fmt(cs.get('suggestion'))!r} | {verdict} |"
            )


def _section_s3(before: dict, after: dict, out: list[str], use_judge: bool) -> None:
    b = before.get("scenarios", {}).get("s3_cold_analyze")
    a = after.get("scenarios", {}).get("s3_cold_analyze")
    if not (b or a):
        return
    out.append("\n\n## S3 — Full cold analysis: richness + latency\n")
    out.append("| | latency p50 | latency p95 | avg findings | avg sources | avg entities |")
    out.append("|---|---|---|---|---|---|")
    for label, x in (("BEFORE", b), ("AFTER", a)):
        if not x:
            continue
        pq = x.get("per_query", [])
        rich = [e.get("richness", {}) for e in pq if e.get("richness", {}).get("present")]
        avg = lambda k: round(sum(r.get(k, 0) for r in rich) / len(rich), 1) if rich else 0  # noqa: E731
        lat = x.get("latency", {})
        out.append(
            f"| {label} | {_fmt(lat.get('p50'))}s | {_fmt(lat.get('p95'))}s | "
            f"{avg('findings')} | {avg('sources')} | {avg('entities')} |"
        )

    if use_judge and b and a:
        from benchmarks import judge

        out.append(
            "\n**Blind pairwise judge** (which answer is richer, bias-cancelled across orderings):\n"
        )
        out.append("| query | winner | rubric before→after (overall) |")
        out.append("|---|---|---|")
        b_by_q = {e["query"]: e for e in b.get("per_query", [])}
        for ae in a.get("per_query", []):
            q = ae["query"]
            be = b_by_q.get(q)
            if not be:
                continue
            pw = judge.pairwise_text(q, be.get("answer", ""), ae.get("answer", ""))
            winner = (pw or {}).get("winner", "—")
            bo = (be.get("judge_rubric") or {}).get("overall", "—")
            ao = (ae.get("judge_rubric") or {}).get("overall", "—")
            mark = {"after": "🟢 after", "before": "🔴 before", "tie": "⚪ tie"}.get(winner, "—")
            out.append(f"| {q[:50]}… | {mark} | {bo} → {ao} |")


def _section_s4_s5(before: dict, after: dict, out: list[str]) -> None:
    for key, title in (
        ("s4_replay", "S4 — Warm replay latency"),
        ("s5_memory", "S5 — Long-term memory (Monday→Friday)"),
    ):
        b = before.get("scenarios", {}).get(key)
        a = after.get("scenarios", {}).get(key)
        if not (b or a):
            continue
        out.append(f"\n\n## {title}\n")
        if key == "s4_replay":
            for label, x in (("BEFORE", b), ("AFTER", a)):
                if x:
                    out.append(
                        f"- {label}: {_fmt(x.get('latency_s'))}s (replay={x.get('is_replay')})"
                    )
        else:
            for label, x in (("BEFORE", b), ("AFTER", a)):
                if not x:
                    continue
                if x.get("error"):
                    out.append(f"- {label}: {x['error']}")
                    continue
                rs = x.get("recall_score", {})
                out.append(
                    f"- {label}: recall_backend={_fmt(x.get('recall_backend'))} · "
                    f"recall@k={rs.get('recall_at_k')} · recalled={rs.get('recalled')} · "
                    f"Friday entities={x.get('friday_richness', {}).get('entities')}"
                )


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Diff two benchmark result sets into a before/after report."
    )
    ap.add_argument("before")
    ap.add_argument("after")
    ap.add_argument("--out", default=None)
    ap.add_argument("--no-judge", action="store_true")
    args = ap.parse_args()

    before, after = _load(args.before), _load(args.after)
    out: list[str] = ["# Emissary — Redis features: before / after\n"]
    out.append(
        f"- **BEFORE** ({before.get('label')}, {before.get('timestamp')}): {_health_line(before.get('health', {}))}"
    )
    out.append(
        f"- **AFTER**  ({after.get('label')}, {after.get('timestamp')}): {_health_line(after.get('health', {}))}"
    )

    # Guard: warn if the two states look identical (e.g. two 'afters').
    if _health_line(before.get("health", {})) == _health_line(after.get("health", {})):
        out.append(
            "\n> ⚠️ **The two runs report the SAME feature state** — this is not a real "
            "before/after. Check that BEFORE was captured with features off."
        )

    _section_s1(before, after, out)
    _section_s2(before, after, out)
    _section_s3(before, after, out, use_judge=not args.no_judge)
    _section_s4_s5(before, after, out)

    report = "\n".join(out) + "\n"
    out_path = Path(args.out) if args.out else Path(args.after).parent / "report.md"
    out_path.write_text(report)
    print(report)
    print(f"\n(wrote {out_path})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
