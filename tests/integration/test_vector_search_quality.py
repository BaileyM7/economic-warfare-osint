"""Does semantic search actually beat lexical? Against a REAL Redis 8.

This is the test that justifies the migration. Everything else proves we degrade
safely; this proves there is something worth degrading *from*.

The golden case, which lexical gets exactly backwards:

    target: Fujian Jinhua  (a DRAM fab, CN)
      SMIC                 (a semiconductor foundry, CN)  <- genuinely similar
      Jinhua Group Holdings(a real-estate developer, CN)  <- shares the token "jinhua"

Token overlap ranks the real-estate firm ABOVE the foundry. Vector recall doesn't.

**Real vectors, no live Voyage.** The vectors in `fixtures/entity_vectors.json`
were recorded once from the real voyage-large-2 API (regenerate with
`scripts/capture_entity_vectors.py` + a key). Replaying them keeps the semantic
quality real — the golden ranking is a genuine result, not a synthetic one —
while making the test deterministic, network-free, and immune to Voyage's ~3 rpm
free-tier rate limit. So this runs in CI with just a Redis 8 container and no key.

Still requires a REAL Redis 8: the point is to exercise real `FT.CREATE` /
`FT.SEARCH` / HNSW / tag filters. fakeredis has no Query Engine, and a mocked
FT.SEARCH would only ever test the mock.

    docker run -d -p 6398:6379 redis:8.2-alpine
    EMISSARY_TEST_REDIS_URL=redis://localhost:6398/0 uv run pytest tests/integration -v

Use port 6398, NOT 6399: `tests/test_rate_limit_storage.py` and
`tests/test_redis_client.py` point at 6399 to assert the "Redis unreachable,
degrade gracefully" path, so a Redis listening there makes those two fail
confusingly.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration

TEST_REDIS_URL = os.getenv("EMISSARY_TEST_REDIS_URL", "")
_FIXTURE = Path(__file__).parent / "fixtures" / "entity_vectors.json"

ENTITIES = [
    # (entity_id, name, type, country, notes)
    ("jinhua", "Fujian Jinhua Integrated Circuit", "company", "CN", "DRAM memory fabrication"),
    ("smic", "SMIC", "company", "CN", "semiconductor foundry, chip manufacturing"),
    ("jinhua_re", "Jinhua Group Holdings", "company", "CN", "real estate development"),
    ("maersk", "Maersk", "company", "DK", "container shipping and logistics"),
]


def _requires_real_redis8():
    if not TEST_REDIS_URL:
        pytest.skip("set EMISSARY_TEST_REDIS_URL to a real Redis 8 to run this")

    import redis as _redis

    try:
        client = _redis.Redis.from_url(TEST_REDIS_URL, socket_connect_timeout=2)
        client.ping()
        client.execute_command("FT._LIST")
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"no Redis Query Engine at {TEST_REDIS_URL}: {exc}")


class _FixtureVectorizer:
    """Replays recorded real voyage-large-2 vectors. Deterministic, no network.

    Exact-match by text: the test controls every string that gets embedded, so a
    miss is a test bug (a new string that wasn't recorded), not something to paper
    over with a zero vector — that would silently corrupt the ranking.
    """

    def __init__(self, table: dict[str, list[float]], dims: int):
        self._table = table
        self.dims = dims
        self.cache = None

    def _one(self, text: str) -> list[float]:
        if text not in self._table:
            raise KeyError(
                f"no recorded vector for {text!r} — re-record fixtures/entity_vectors.json"
            )
        return self._table[text]

    async def aembed_many(self, texts):
        return [self._one(t) for t in texts]

    async def aembed(self, text):
        return self._one(text)

    def embed_many(self, texts):
        return [self._one(t) for t in texts]

    def embed(self, text):
        return self._one(text)


@pytest.fixture(scope="module")
def indexed(app_module):
    """Seed SQLite, inject recorded vectors, build the index — ONCE per module.

    Module-scoped: `app_client` wipes the DB per test, which would rebuild the
    index every time for nothing.
    """
    _requires_real_redis8()
    if not _FIXTURE.exists():
        pytest.skip(f"missing {_FIXTURE} — run scripts/capture_entity_vectors.py")

    from src.common import embeddings, knowledge_store as ks, redis_client, vector_index
    from src.common.config import config

    data = json.loads(_FIXTURE.read_text())

    original_url = config.redis_url
    original_model = config.embedding_model
    original_dims = config.embedding_dims
    # The meta guard compares against config, so config must match the fixture.
    config.redis_url = TEST_REDIS_URL
    config.embedding_model = data["model"]
    config.embedding_dims = data["dims"]

    redis_client.reset_for_tests()
    vector_index.reset_for_tests()
    embeddings.set_vectorizer_for_tests(_FixtureVectorizer(data["vectors"], data["dims"]))
    vector_index.drop_index()

    for eid, name, etype, country, notes in ENTITIES:
        ks.upsert_entity(entity_id=eid, name=name, entity_type=etype, country=country, notes=notes)

    assert vector_index.ensure_index(), "could not create the index against real Redis"

    import asyncio

    # asyncio.run(), not get_event_loop(): a sync module-scoped fixture must not
    # depend on the ambient loop, which pytest-asyncio may have already closed
    # after an earlier async integration module ran in the same session.
    result = asyncio.run(vector_index.backfill(ks.list_entities()))
    assert result["indexed"] >= len(ENTITIES), f"backfill failed: {result}"

    yield ks

    for eid, *_ in ENTITIES:
        ks.delete_entity(eid)
    vector_index.drop_index()
    config.redis_url = original_url
    config.embedding_model = original_model
    config.embedding_dims = original_dims
    embeddings.reset_for_tests()
    redis_client.reset_for_tests()
    vector_index.reset_for_tests()


@pytest.mark.asyncio
async def test_semantic_beats_lexical_on_the_golden_case(indexed):
    """SMIC must outrank Jinhua Group Holdings. Lexical gets this backwards.

    Real recorded scores (voyage-large-2), target = Fujian Jinhua:
        SMIC                  semantic 0.910  lexical 0.250
        Jinhua Group Holdings semantic 0.872  lexical 0.400   <- the token collision

    Also why ranking is semantic-only: blending in 30% lexical put the real-estate
    firm back on top (0.730 vs 0.712). See similarity.SEMANTIC_WEIGHT.
    """
    from src.common import similarity as sim

    ks = indexed
    entities = ks.list_entities()
    target = ks.get_entity("jinhua")

    # 1. Today's behaviour — the bug, asserted so the premise stays honest.
    lexical_results, lexical_backend = sim.rank_similar(
        target, [e for e in entities if e["entity_id"] != "jinhua"], top_k=3
    )
    assert lexical_backend == "lexical"
    lexical_order = [r["entity"]["entity_id"] for r in lexical_results]
    assert lexical_order.index("jinhua_re") < lexical_order.index("smic"), (
        "the premise of this migration is that lexical ranks the real-estate firm "
        f"above the foundry; it no longer does: {lexical_order}"
    )

    # 2. The fix.
    hybrid_results, hybrid_backend = await sim.rank_similar_indexed(target, top_k=3)
    assert hybrid_backend == "hybrid", "expected the real index, not a fallback"
    hybrid_order = [r["entity"]["entity_id"] for r in hybrid_results]
    assert hybrid_order.index("smic") < hybrid_order.index("jinhua_re"), (
        f"semantic search should rank the foundry above the real-estate firm: {hybrid_order}"
    )


@pytest.mark.asyncio
async def test_hybrid_results_stay_explainable(indexed):
    """A cosine with no rationale is a regression for an intel product."""
    from src.common import similarity as sim

    results, backend = await sim.rank_similar_indexed(indexed.get_entity("jinhua"), top_k=3)
    assert backend == "hybrid"

    basis = results[0]["basis"]
    # Every pre-existing key survives — the frontend renders these.
    for key in ("name_overlap", "same_type", "same_country", "shared_identifiers", "shared_terms"):
        assert key in basis, f"the explainable basis lost {key}"
    assert 0.0 <= basis["semantic_score"] <= 1.0
    assert "why" in basis and basis["why"]


@pytest.mark.asyncio
async def test_tag_filter_restricts_recall(indexed):
    """A country tag filter must actually constrain the KNN candidate set."""
    from src.common import vector_index

    hits = await vector_index.search_entity_ids("semiconductor manufacturer", country="DK")
    assert hits is not None
    assert {eid for eid, _ in hits} <= {"maersk"}, "country tag filter was not applied"


@pytest.mark.asyncio
async def test_unchanged_text_skips_re_embedding(indexed):
    """The cost control: re-indexing unchanged entities must spend nothing."""
    from src.common import vector_index

    again = await vector_index.backfill(indexed.list_entities())
    assert again["indexed"] == 0, "re-indexing unchanged entities should be free"
    assert again["skipped"] == len(ENTITIES)


@pytest.mark.asyncio
async def test_write_through_makes_a_new_entity_searchable(indexed):
    """Saving an entity puts it in the index without an explicit reindex."""
    from src.common import vector_index

    ks = indexed
    ks.upsert_entity(
        entity_id="tsmc",
        name="TSMC",
        entity_type="company",
        country="TW",
        notes="advanced semiconductor foundry",
    )
    try:
        # upsert is non-blocking by design, so drain the queue the way boot does.
        drained = await vector_index.drain_dirty()
        assert drained["indexed"] >= 1, f"the dirty set should have caught it: {drained}"

        hits = await vector_index.search_entity_ids("chip foundry")
        assert hits is not None
        assert "tsmc" in {eid for eid, _ in hits}
    finally:
        ks.delete_entity("tsmc")
