"""Safe near-match "Did you mean…?" suggestion (Phase 6 semantic upgrade).

Guards the contract that a close-but-not-exact query surfaces the nearest
fast-replay query as a *suggestion only* — never an auto-answer, and never the
wrong entity's assessment. Exact matches return null (the analyze endpoint
already instant-replays those).

Runs on the default hermetic config (no embeddings), so the suggest path here is
the LEXICAL (Jaccard) fallback — which is what prod runs until a Voyage key is
set. The semantic upgrade + the entity-swap safety are covered against real
vectors in tests/test_semantic_cache.py and the integration suite.
"""

from __future__ import annotations

import pytest

from src.routers.orchestrator import _DEMO_QUERIES, _suggest_query


@pytest.mark.asyncio
async def test_suggest_returns_nearest_warmed_query_for_reworded_input():
    # A reworded version of a demo query (key terms retained) → suggest the
    # canonical warmed string. On the lexical fallback this relies on token
    # overlap; the semantic path (real vectors) catches pure synonym swaps too.
    reworded = "What sanctions exposures does Nuctech have and who owns it?"
    suggestion, score, backend = await _suggest_query(reworded)
    assert suggestion == _DEMO_QUERIES[1]  # the canonical Nuctech question
    assert score >= 0.6
    assert backend == "lexical"  # no embeddings in tests


@pytest.mark.asyncio
async def test_suggest_never_returns_exact_match():
    # Exact (normalized) match → null; replay handles it, no need to suggest.
    exact = _DEMO_QUERIES[0].upper()  # case-insensitive normalize
    suggestion, score, _ = await _suggest_query(exact)
    assert suggestion is None
    assert score == 0.0


@pytest.mark.asyncio
async def test_suggest_returns_none_for_unrelated_query():
    suggestion, score, _ = await _suggest_query("What is the GDP of Brazil in 2021?")
    assert suggestion is None
    assert score == 0.0


def test_suggest_endpoint_contract(app_client, auth_headers):
    # Unrelated → no suggestion. (backend field is additive.)
    r1 = app_client.post(
        "/api/analyze/suggest",
        json={"query": "weather in Antarctica next week"},
        headers=auth_headers,
    )
    assert r1.status_code == 200
    body1 = r1.json()
    assert body1["suggestion"] is None
    assert body1["score"] == 0.0

    # Near-match to a demo query → suggestion + score, but only ever a suggestion.
    r2 = app_client.post(
        "/api/analyze/suggest",
        json={"query": "how exposed is the drone supply chain to a ban on DJI exports"},
        headers=auth_headers,
    )
    assert r2.status_code == 200
    body = r2.json()
    assert body["suggestion"] == _DEMO_QUERIES[2]  # the DJI drone question
    assert body["score"] >= 0.6

    # Empty query → null, no crash.
    r3 = app_client.post("/api/analyze/suggest", json={"query": ""}, headers=auth_headers)
    assert r3.status_code == 200
    assert r3.json()["suggestion"] is None
