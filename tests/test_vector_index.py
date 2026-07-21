"""Entity vector index: degradation, write-through safety, and explainability.

The `FT.*` path itself is NOT faked here. fakeredis has no Query Engine, and a
hand-rolled FT.SEARCH mock would only test the mock. Instead:

  * these tests pin the **fallback** (which is what the whole existing suite runs
    on, and what prod runs on until a real Redis 8 exists), plus the guards that
    must hold regardless of backend;
  * `tests/integration/test_vector_search_quality.py` exercises real FT.* against
    a real redis:8 container, and is skipped when one isn't present.

The load-bearing test here is `test_upsert_never_blocks_or_raises`: the write-through
must never be able to fail a write to the system of record.
"""

from __future__ import annotations

import pytest

from src.common import knowledge_store as ks
from src.common import similarity as sim
from src.common import vector_index


@pytest.fixture(autouse=True)
def _reset():
    vector_index.reset_for_tests()
    yield
    vector_index.reset_for_tests()


def _ent(entity_id, name, entity_type="company", country=None, notes=""):
    return {
        "entity_id": entity_id,
        "name": name,
        "entity_type": entity_type,
        "country": country,
        "aliases": [],
        "identifiers": {},
        "notes": notes,
    }


# --- Degradation (this is what CI and today's prod actually run) -------------


def test_unavailable_without_redis_and_embeddings():
    # conftest forces REDIS_URL="" and there's no VOYAGE_API_KEY in tests.
    assert vector_index.is_available() is False
    assert vector_index.get_index() is None


@pytest.mark.asyncio
async def test_search_returns_none_when_unavailable():
    """None means 'could not search' — distinct from [] meaning 'found nothing'.

    Callers branch on it to fall back, so conflating the two would silently turn a
    Redis outage into 'no similar companies exist'.
    """
    assert await vector_index.search_entity_ids("Fujian Jinhua") is None


@pytest.mark.asyncio
async def test_rank_similar_indexed_falls_back_to_lexical(app_client, auth_headers):
    ks.upsert_entity(**_ent("smic", "SMIC", country="CN", notes="semiconductor foundry"))
    ks.upsert_entity(**_ent("jh", "Fujian Jinhua", country="CN", notes="DRAM fabrication"))

    results, backend = await sim.rank_similar_indexed(ks.get_entity("jh"), top_k=5)
    assert backend == "lexical"  # no index -> honest about it
    assert results, "the fallback must still return a real ranking"
    # And the classic basis keys survive the fallback path.
    assert "name_overlap" in results[0]["basis"]


@pytest.mark.asyncio
async def test_knowledge_store_search_falls_back_to_like(app_client, auth_headers):
    ks.upsert_entity(**_ent("acme", "Acme Corp"))
    entities, backend = await ks.search_entities("Acme")
    assert backend == "lexical"
    assert [e["entity_id"] for e in entities] == ["acme"]


def test_list_entities_like_semantics_are_unchanged(app_client, auth_headers):
    """`list_entities` stays a substring FILTER — graph tools + discovery rely on it."""
    ks.upsert_entity(**_ent("a", "Fujian Jinhua"))
    ks.upsert_entity(**_ent("b", "Jinhua Group Holdings"))
    ks.upsert_entity(**_ent("c", "SMIC"))

    got = {e["entity_id"] for e in ks.list_entities(q="jinhua")}
    assert got == {"a", "b"}  # substring match, not a ranked/semantic result


# --- The write-through must never endanger the system of record --------------


def test_upsert_never_blocks_or_raises(app_client, auth_headers, monkeypatch):
    """A broken index must not break saving an entity.

    upsert_entity is sync and is the write path for BOTH the HTTP router and the
    agent's graph tools. Embedding is a network call; if a Voyage timeout could
    propagate here, "the index is slow" would become "saving an entity failed".
    """

    def _boom(_entity):
        raise RuntimeError("redis is on fire")

    monkeypatch.setattr(vector_index, "enqueue_reindex", _boom)

    entity, created = ks.upsert_entity(**_ent("safe", "Still Saved Corp"))
    assert created is True
    assert entity["name"] == "Still Saved Corp"
    assert ks.get_entity("safe") is not None  # durable regardless of the index


def test_delete_never_raises_when_index_is_broken(app_client, auth_headers, monkeypatch):
    ks.upsert_entity(**_ent("gone", "Delete Me"))

    def _boom(_entity_id):
        raise RuntimeError("redis is on fire")

    monkeypatch.setattr(vector_index, "enqueue_removal", _boom)
    assert ks.delete_entity("gone") == 1
    assert ks.get_entity("gone") is None


def test_enqueue_reindex_is_a_noop_without_redis():
    vector_index.enqueue_reindex(_ent("x", "X Corp"))  # must not raise


def test_text_hash_is_stable_and_content_addressed():
    """Unchanged text => same hash => the re-save skips the embedding call.

    This is the cost control: the agent re-saves the same entities on every run.
    """
    a = vector_index.text_hash(sim.entity_text(_ent("x", "Acme", notes="chips")))
    b = vector_index.text_hash(sim.entity_text(_ent("x", "Acme", notes="chips")))
    c = vector_index.text_hash(sim.entity_text(_ent("x", "Acme", notes="ships")))
    assert a == b
    assert a != c


# --- Batch hydrate -----------------------------------------------------------


def test_get_entities_by_ids_preserves_rank_order(app_client, auth_headers):
    """Hits come back ranked; hydration must not reshuffle them."""
    for eid in ("a", "b", "c"):
        ks.upsert_entity(**_ent(eid, f"Corp {eid.upper()}"))

    got = ks.get_entities_by_ids(["c", "a", "b"])
    assert [e["entity_id"] for e in got] == ["c", "a", "b"]


def test_get_entities_by_ids_skips_missing(app_client, auth_headers):
    """An id in the index but gone from SQLite must not fabricate a result."""
    ks.upsert_entity(**_ent("real", "Real Corp"))
    got = ks.get_entities_by_ids(["real", "deleted-from-sqlite"])
    assert [e["entity_id"] for e in got] == ["real"]


def test_get_entities_by_ids_empty():
    assert ks.get_entities_by_ids([]) == []


# --- Explainability ----------------------------------------------------------


def test_explain_warns_when_the_match_is_semantic_only():
    """High cosine + zero lexical evidence must SAY so.

    This is the case where a vector is doing real work and is also most likely to
    be subtly wrong. "0.87" alone reads as confidence; an analyst needs the caveat.
    """
    basis = {
        "shared_terms": [],
        "shared_identifiers": [],
        "same_type": False,
        "same_country": False,
    }
    why = sim._explain(basis, semantic=0.87, lexical=0.0)
    assert "Verify before citing" in why
    assert "0.87" in why


def test_explain_cites_the_evidence_when_there_is_some():
    basis = {
        "shared_terms": ["semiconductor", "fab"],
        "shared_identifiers": ["LEI:123"],
        "same_type": True,
        "same_country": True,
    }
    why = sim._explain(basis, semantic=0.91, lexical=0.4)
    assert "semiconductor" in why
    assert "LEI:123" in why
    assert "same type" in why and "same country" in why


def test_explain_flags_a_weak_match():
    basis = {
        "shared_terms": [],
        "shared_identifiers": [],
        "same_type": False,
        "same_country": False,
    }
    why = sim._explain(basis, semantic=0.2, lexical=0.0)
    assert "Low confidence" in why


# --- Meta guard --------------------------------------------------------------


def test_meta_mismatch_disables_the_index():
    """An index built for a different model/dims must never be queried.

    A dimension mismatch against HNSW doesn't error — it returns nonsense
    neighbours. Refusing is the only safe behaviour.
    """
    import json

    class _FakeClient:
        def get(self, _key):
            return json.dumps({"model": "voyage-3", "dims": 1024, "schema_v": 1})

        def set(self, *a, **kw):
            return True

    assert vector_index._check_meta(_FakeClient()) is False
    assert vector_index._meta_error
    assert "reindex_entities" in vector_index._meta_error  # tells you how to fix it
    assert vector_index.is_available() is False  # and stays disabled


def test_meta_match_is_allowed():
    import json

    from src.common.config import config

    class _FakeClient:
        def get(self, _key):
            return json.dumps(
                {
                    "model": config.embedding_model,
                    "dims": config.embedding_dims,
                    "schema_v": vector_index.SCHEMA_VERSION,
                }
            )

        def set(self, *a, **kw):
            return True

    assert vector_index._check_meta(_FakeClient()) is True
    assert vector_index._meta_error == ""
