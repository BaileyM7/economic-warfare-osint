"""Safe near-match "Did you mean…?" suggestion (cache Part A3).

Guards the contract that a close-but-not-exact query surfaces the nearest
fast-replay query as a *suggestion only* — never an auto-answer, and never the
wrong entity's assessment. Exact matches return null (the analyze endpoint
already instant-replays those).
"""

from __future__ import annotations

from src.routers.orchestrator import (
    _DEMO_QUERIES,
    _query_similarity,
    _suggest_query,
)


def test_query_similarity_is_symmetric_and_bounded():
    a = "Who ultimately owns Nuctech, and what are its sanctions exposures?"
    assert _query_similarity(a, a) == 1.0  # identical content tokens
    assert _query_similarity(a, "") == 0.0
    # Symmetric.
    b = "Nuctech ownership and sanctions"
    assert _query_similarity(a, b) == _query_similarity(b, a)
    # Unrelated queries score low.
    assert _query_similarity("Map Rosatom subsidiaries", a) < 0.3


def test_suggest_returns_nearest_warmed_query_for_reworded_input():
    # A reworded version of a demo query (key terms retained) → suggest the
    # canonical warmed string. (Pure synonym swaps are a known lexical limit —
    # the embedding upgrade noted in the plan would catch those.)
    reworded = "What sanctions exposures does Nuctech have and who owns it?"
    suggestion, score = _suggest_query(reworded)
    assert suggestion == _DEMO_QUERIES[1]  # the canonical Nuctech question
    assert score >= 0.6


def test_suggest_never_returns_exact_match():
    # Exact (normalized) match → null; replay handles it, no need to suggest.
    exact = _DEMO_QUERIES[0].upper()  # case-insensitive normalize
    suggestion, score = _suggest_query(exact)
    assert suggestion is None
    assert score == 0.0


def test_suggest_returns_none_for_unrelated_query():
    suggestion, score = _suggest_query("What is the GDP of Brazil in 2021?")
    assert suggestion is None
    assert score == 0.0


def test_suggest_endpoint_contract(app_client, auth_headers):
    # Unrelated → no suggestion.
    r1 = app_client.post(
        "/api/analyze/suggest",
        json={"query": "weather in Antarctica next week"},
        headers=auth_headers,
    )
    assert r1.status_code == 200
    assert r1.json() == {"suggestion": None, "score": 0.0}

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
