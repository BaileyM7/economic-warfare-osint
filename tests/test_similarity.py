"""Tests for entity similarity / link prediction (issue #30).

Covers the dependency-free lexical backend (module + endpoint) and the shared
tokenizer that the orchestrator's query-suggest seam now delegates to. The
embedding backend isn't installed in CI, so we only assert its graceful
fallback path, not real vectors.
"""

from __future__ import annotations

from src.common import similarity as sim


def test_jaccard_matches_legacy_orchestrator_behavior():
    # The orchestrator's _query_similarity now delegates here; pin the basics.
    assert sim.jaccard_similarity("Nuctech ownership", "Nuctech ownership") == 1.0
    assert sim.jaccard_similarity("anything", "") == 0.0
    a, b = "Map Rosatom subsidiaries", "Nuctech sanctions exposure"
    assert sim.jaccard_similarity(a, b) == sim.jaccard_similarity(b, a)


def test_entity_text_flattens_traits():
    e = {
        "name": "Acme Corp",
        "entity_type": "company",
        "country": "US",
        "aliases": ["Acme Inc"],
        "identifiers": {"lei": "ABC123"},
        "notes": "semiconductor",
    }
    text = sim.entity_text(e)
    for token in ("Acme", "company", "US", "ABC123", "semiconductor"):
        assert token in text


def _e(entity_id, name, entity_type="company", country=None, notes=""):
    return {
        "entity_id": entity_id,
        "name": name,
        "entity_type": entity_type,
        "country": country,
        "aliases": [],
        "identifiers": {},
        "notes": notes,
    }


def test_rank_similar_orders_by_characteristics_and_explains():
    target = _e(
        "smic", "Semiconductor Manufacturing International", "company", "CN", "chip foundry"
    )
    candidates = [
        _e("tsmc", "Taiwan Semiconductor Manufacturing", "company", "TW", "chip foundry"),
        _e("ymtc", "Yangtze Memory Technologies", "company", "CN", "memory chips"),
        _e("maersk", "Maersk Line", "company", "DK", "shipping"),
        target,  # must be excluded (same entity_id)
    ]
    results, backend = sim.rank_similar(target, candidates, top_k=3, backend="lexical")
    assert backend == "lexical"
    ids = [r["entity"]["entity_id"] for r in results]
    assert "smic" not in ids  # target excluded
    # The two semiconductor companies should outrank the shipping line.
    assert "maersk" not in ids[:2]
    # Explainable basis present.
    assert "name_overlap" in results[0]["basis"]
    assert results[0]["score"] >= results[-1]["score"]


def test_embedding_backend_falls_back_to_lexical_when_dep_missing():
    target = _e("a", "Alpha Corp")
    cands = [_e("b", "Beta Corp")]
    # sentence-transformers isn't installed in CI → graceful fallback, no raise.
    results, backend = sim.rank_similar(target, cands, top_k=1, backend="embedding")
    assert backend == "lexical"
    assert len(results) == 1


# --- Endpoint ---------------------------------------------------------------


def _save(client, headers, entity_id, name, entity_type="company", country=None, notes=""):
    return client.post(
        "/api/knowledge/entities",
        json={
            "entity_id": entity_id,
            "name": name,
            "entity_type": entity_type,
            "country": country,
            "notes": notes,
        },
        headers=headers,
    )


def test_similar_endpoint_ranks_saved_entities(app_client, auth_headers):
    _save(app_client, auth_headers, "smic", "SMIC", "company", "CN", "chip foundry")
    _save(app_client, auth_headers, "ymtc", "YMTC chip memory", "company", "CN", "chip memory")
    _save(app_client, auth_headers, "maersk", "Maersk Line", "company", "DK", "shipping")

    r = app_client.post(
        "/api/entity/similar", json={"entity_id": "smic", "top_k": 2}, headers=auth_headers
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["target"]["entity_id"] == "smic"
    assert body["backend_used"] == "lexical"
    assert body["count"] == 2
    # target itself never appears in results
    assert all(res["entity"]["entity_id"] != "smic" for res in body["results"])


def test_similar_endpoint_by_name_adhoc_target(app_client, auth_headers):
    _save(app_client, auth_headers, "ymtc", "Yangtze Memory", "company", "CN", "memory chips")
    r = app_client.post(
        "/api/entity/similar",
        json={"name": "Memory chip maker", "entity_type": "company", "top_k": 1},
        headers=auth_headers,
    )
    assert r.status_code == 200, r.text
    assert r.json()["count"] == 1


def test_similar_endpoint_requires_a_target(app_client, auth_headers):
    r = app_client.post("/api/entity/similar", json={"top_k": 5}, headers=auth_headers)
    assert r.status_code == 400


def test_similar_endpoint_unknown_entity_id_404(app_client, auth_headers):
    r = app_client.post("/api/entity/similar", json={"entity_id": "nope"}, headers=auth_headers)
    assert r.status_code == 404
