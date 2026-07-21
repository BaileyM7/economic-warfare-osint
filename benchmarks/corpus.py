"""The benchmark corpus + labeled golden data.

Everything the before/after suite runs. Kept as plain data so a run is fully
reproducible and the labels (what a "correct" result looks like) live in one
place. The queries and golden sets are copied from — and must stay in lockstep
with — the app's own fixtures:
  * DEMO_QUERIES  ↔ src/routers/orchestrator.py `_DEMO_QUERIES`
  * ENTITY_*      ↔ tests/integration/fixtures/entity_vectors.json + the golden
                    ranking asserted in tests/integration/test_vector_search_quality.py
  * MEMORY_*      ↔ tests/integration/fixtures/memory_vectors.json + the Monday→
                    Friday recall asserted in tests/integration/test_memory_recall.py
  * SUGGEST_*     ↔ scripts/calibrate_semantic_cache.py labeled pairs
"""

from __future__ import annotations

# --- The 4 pre-warmed "Ask Anything" demo queries -----------------------------
# EXACT text matters: the prewarm cache keys on the normalized string, and
# "Rosatom's" uses a curly apostrophe (U+2019). Copy verbatim.
DEMO_QUERIES: list[str] = [
    "What happens to global semiconductor supply if we sanction Fujian Jinhua?",
    "Who ultimately owns Nuctech, and what are its sanctions exposures?",
    "How exposed is the drone supply chain to a DJI export ban?",
    "Map Rosatom’s subsidiaries and their Western trade links.",
]

# --- S1: entity similarity golden set -----------------------------------------
# Target is a DRAM fab. The decoy ("Jinhua Group Holdings", a real-estate firm)
# shares the token "jinhua", so LEXICAL ranks it ABOVE the genuinely-similar
# foundry (SMIC). Semantic search must invert that. `expect_outranks` = pairs
# (a, b) where a MUST rank above b in a correct result.
ENTITY_SIMILARITY = {
    "seed_entities": [
        # entity_id, name, entity_type, country, notes
        (
            "bench_jinhua",
            "Fujian Jinhua Integrated Circuit",
            "company",
            "CN",
            "DRAM memory fabrication",
        ),
        ("bench_smic", "SMIC", "company", "CN", "semiconductor foundry, chip manufacturing"),
        ("bench_jinhua_re", "Jinhua Group Holdings", "company", "CN", "real estate development"),
        ("bench_maersk", "Maersk", "company", "DK", "container shipping and logistics"),
        ("bench_tsmc", "TSMC", "company", "TW", "advanced semiconductor foundry"),
    ],
    "target_id": "bench_jinhua",
    "target_name": "Fujian Jinhua Integrated Circuit",
    # The migration's whole point: the foundry must beat the real-estate decoy.
    "expect_outranks": [("bench_smic", "bench_jinhua_re")],
    # Semiconductor peers that should appear in the top results at all.
    "relevant_ids": {"bench_smic", "bench_tsmc"},
    "decoy_ids": {"bench_jinhua_re", "bench_maersk"},
}

# --- S2: semantic suggest / "Did you mean?" -----------------------------------
# Each: (input query, expected canonical demo query it should map to OR None).
# The reworded ones SHOULD map to their demo query on the vector backend;
# lexical may miss pure rephrasings. The entity-swap adversaries must NOT be
# offered as a suggestion for a DIFFERENT company's warmed question.
SUGGEST_CASES = [
    {
        "label": "paraphrase",
        "query": "What sanctions exposures does Nuctech have, and who ultimately owns it?",
        "expect_maps_to": "Who ultimately owns Nuctech, and what are its sanctions exposures?",
    },
    {
        "label": "paraphrase",
        "query": "If Fujian Jinhua were sanctioned, how would global chip supply be affected?",
        "expect_maps_to": "What happens to global semiconductor supply if we sanction Fujian Jinhua?",
    },
    {
        "label": "entity_swap",  # must NOT be offered the Nuctech answer
        "query": "Who ultimately owns Hikvision, and what are its sanctions exposures?",
        "must_not_map_to": "Who ultimately owns Nuctech, and what are its sanctions exposures?",
    },
    {
        "label": "entity_swap",  # must NOT be offered the Fujian Jinhua answer
        "query": "What happens to global semiconductor supply if we sanction SMIC?",
        "must_not_map_to": "What happens to global semiconductor supply if we sanction Fujian Jinhua?",
    },
    {
        "label": "unrelated",
        "query": "What is the GDP of Brazil in 2021?",
        "expect_maps_to": None,
    },
]

# --- S5: long-term memory Monday → Friday -------------------------------------
# Monday seeds facts (via a real analysis under `session_id`); Friday asks an
# anaphoric follow-on in the SAME session. A correct AFTER run recalls the
# semiconductor supply-chain entities; BEFORE has no long-term memory at all.
MEMORY_SCENARIO = {
    "monday_query": "What happens to global semiconductor supply if we sanction Fujian Jinhua?",
    "friday_query": "What else is exposed to that supply chain?",
    # The anaphora ("that supply chain") should resolve to these via recall.
    "expect_recalled_entities": ["Fujian Jinhua", "UMC", "Micron"],
    "expect_excluded_entities": ["Maersk"],  # a shipping distractor must not dominate
}

# --- Which warmed query to use for the S4 replay-latency probe -----------------
REPLAY_QUERY = DEMO_QUERIES[0]
