"""Calibrate the semantic pre-warm cache thresholds against real Voyage vectors.

Prints the similarity distribution for three labeled classes, so the auto-replay
threshold is picked from data, not vibes:

  * paraphrase  — a true rewording of a warmed demo query (SHOULD auto-replay)
  * entity_swap — same question shape, different company (must NEVER auto-replay)
  * unrelated   — a different question entirely

The entity_swap row is the adversary this whole design exists to defend against.
If its similarity band overlaps paraphrase (it does — ~0.897 vs ~0.960 measured),
that's the proof that a distance threshold alone is unsafe and the entity-
signature gate is load-bearing.

    VOYAGE_API_KEY=... EMBEDDING_MODEL=voyage-large-2 uv run python -m scripts.calibrate_semantic_cache
"""

from __future__ import annotations

import asyncio
import sys

# (label, query_a, query_b)
PAIRS = [
    (
        "paraphrase",
        "Who ultimately owns Nuctech, and what are its sanctions exposures?",
        "What sanctions exposures does Nuctech have, and who ultimately owns it?",
    ),
    (
        "paraphrase",
        "What happens to global semiconductor supply if we sanction Fujian Jinhua?",
        "If Fujian Jinhua were sanctioned, how would chip supply be affected?",
    ),
    (
        "entity_swap",
        "Who ultimately owns Nuctech, and what are its sanctions exposures?",
        "Who ultimately owns Hikvision, and what are its sanctions exposures?",
    ),
    (
        "entity_swap",
        "What happens to global semiconductor supply if we sanction Fujian Jinhua?",
        "What happens to global semiconductor supply if we sanction SMIC?",
    ),
    (
        "entity_swap",
        "How exposed is the drone supply chain to a DJI export ban?",
        "How exposed is the drone supply chain to an Autel export ban?",
    ),
    (
        "unrelated",
        "Who ultimately owns Nuctech, and what are its sanctions exposures?",
        "What is the GDP of Brazil in 2021?",
    ),
]


async def main() -> int:
    from src.common import embeddings
    from src.common.semantic_cache import (
        AUTO_REPLAY_MIN_SIMILARITY,
        _cosine,
        entity_signature,
    )

    if not embeddings.embeddings_enabled():
        print(f"embeddings off: {embeddings.embeddings_status()['reason']}", file=sys.stderr)
        return 2

    texts = sorted({t for _, a, b in PAIRS for t in (a, b)})
    vecs = await embeddings.embed_many(texts)
    if vecs is None:
        print("Voyage call failed/rate-limited — retry in a minute.", file=sys.stderr)
        return 1
    table = dict(zip(texts, vecs))

    print(
        f"threshold: auto-replay requires similarity >= {AUTO_REPLAY_MIN_SIMILARITY} AND same entity signature\n"
    )
    print(f"{'class':<12} {'sim':>6}  {'sig=':<5} {'would auto-replay?':<18} pair")
    print("-" * 88)
    swap_max = 0.0
    para_min = 1.0
    for label, a, b in PAIRS:
        sim = _cosine(table[a], table[b])
        sig_match = entity_signature(a) == entity_signature(b)
        # The gate: threshold AND signature.
        replays = sim >= AUTO_REPLAY_MIN_SIMILARITY and sig_match
        print(
            f"{label:<12} {sim:>6.3f}  {str(sig_match):<5} {('YES' if replays else 'no'):<18} {a[:32]}… / {b[:24]}…"
        )
        if label == "entity_swap":
            swap_max = max(swap_max, sim)
        if label == "paraphrase":
            para_min = min(para_min, sim)

    print("-" * 88)
    print(f"paraphrase min similarity: {para_min:.3f}")
    print(f"entity_swap max similarity: {swap_max:.3f}")
    if swap_max >= para_min:
        print(
            "\n=> entity_swap OVERLAPS paraphrase on raw similarity. A distance threshold alone "
            "cannot separate them — the entity-signature gate is REQUIRED, and no entity_swap "
            "above should ever show 'YES'."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
