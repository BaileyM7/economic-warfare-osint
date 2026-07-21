"""Long-term memory semantic recall against a REAL Redis 8.

The unit tests (tests/test_long_term_memory.py) prove the SQLite system-of-record,
the poisoning guard, dedup, isolation, and the lexical fallback. This proves the
part that needs the Query Engine: the per-user vector index actually retrieves the
right memory for an anaphoric query with little lexical overlap, and stays scoped
to the user.

Real recorded vectors (fixtures/memory_vectors.json, from voyage-large-2 — see
scripts/capture_memory_vectors.py), so it runs in CI against a Redis 8 container
with no Voyage key and no rate-limit flakiness. Real FT.* though: fakeredis has no
Query Engine.

    docker run -d -p 6398:6379 redis:8.2-alpine
    EMISSARY_TEST_REDIS_URL=redis://localhost:6398/0 uv run pytest tests/integration -v
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration

TEST_REDIS_URL = os.getenv("EMISSARY_TEST_REDIS_URL", "")
_FIXTURE = Path(__file__).parent / "fixtures" / "memory_vectors.json"

# The Monday facts. entity_names get lowercased on store; the fixture was recorded
# from the same sanitized search text, so the injected vectorizer matches.
MEMORIES = [
    ("Fujian Jinhua is on the BIS Entity List (2018).", ["Fujian Jinhua"], "HIGH", "OFAC SDN"),
    (
        "Fujian Jinhua's DRAM production line was transferred from UMC.",
        ["Fujian Jinhua", "UMC"],
        "MEDIUM",
        "Sayari",
    ),
    (
        "Micron is the Western supplier displaced by Fujian Jinhua's DRAM capacity.",
        ["Fujian Jinhua", "Micron"],
        "MEDIUM",
        "OpenCorporates",
    ),
    ("Maersk runs container shipping on the Asia-Europe lane.", ["Maersk"], "HIGH", "GLEIF"),
]
FRIDAY_QUERY = "what else is exposed to that supply chain?"


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
    """Replays recorded real voyage-large-2 vectors. Exact-match; miss = test bug."""

    def __init__(self, table, dims):
        self._table = table
        self.dims = dims
        self.cache = None

    def _one(self, text):
        if text not in self._table:
            raise KeyError(f"no recorded vector for {text!r} — re-record memory_vectors.json")
        return self._table[text]

    async def aembed_many(self, texts):
        return [self._one(t) for t in texts]

    async def aembed(self, text):
        return self._one(text)

    def embed_many(self, texts):
        return [self._one(t) for t in texts]

    def embed(self, text):
        return self._one(text)


@pytest.fixture
def seeded(app_client):
    """Fresh throwaway DB (app_client wipes it), Redis pointed at the real 8, and
    Alice's four Monday memories indexed with recorded vectors."""
    _requires_real_redis8()
    if not _FIXTURE.exists():
        pytest.skip(f"missing {_FIXTURE} — run scripts/capture_memory_vectors.py")

    from src.common import agent_memory, embeddings, redis_client
    from src.common.agent_memory import Memory
    from src.common.config import config

    data = json.loads(_FIXTURE.read_text())

    saved = (config.redis_url, config.embedding_model, config.embedding_dims)
    config.redis_url = TEST_REDIS_URL
    config.embedding_model = data["model"]
    config.embedding_dims = data["dims"]

    redis_client.reset_for_tests()
    agent_memory.reset_for_tests()
    embeddings.set_vectorizer_for_tests(_FixtureVectorizer(data["vectors"], data["dims"]))

    # Clear any leftover memory index/docs from a prior run.
    try:
        client = redis_client.get_redis()
        client.execute_command("FT.DROPINDEX", agent_memory._MEM_INDEX_NAME, "DD")
    except Exception:  # noqa: BLE001
        pass
    for k in redis_client.get_redis().keys("mem:*"):
        redis_client.get_redis().delete(k)

    agent_memory.remember(
        "alice",
        [
            Memory(text=t, entity_names=e, confidence=c, sources=[{"name": s}])
            for t, e, c, s in MEMORIES
        ],
    )

    yield agent_memory

    for k in redis_client.get_redis().keys("mem:*"):
        redis_client.get_redis().delete(k)
    try:
        redis_client.get_redis().execute_command("FT.DROPINDEX", agent_memory._MEM_INDEX_NAME, "DD")
    except Exception:  # noqa: BLE001
        pass
    config.redis_url, config.embedding_model, config.embedding_dims = saved
    embeddings.reset_for_tests()
    agent_memory.reset_for_tests()
    redis_client.reset_for_tests()


@pytest.mark.asyncio
async def test_semantic_recall_backend_is_redis(seeded):
    assert seeded.memory_backend_name() == "redis"


@pytest.mark.asyncio
async def test_bare_query_recall_surfaces_supply_chain_facts(seeded):
    """Bare-query semantic recall returns supply-chain-relevant memories.

    Note it does NOT (and shouldn't) exclude the shipping fact: "that supply
    chain" embeds close to logistics/shipping too, so Maersk is a legitimately
    relevant hit for the bare string. Disambiguating "*that* supply chain" to the
    SEMICONDUCTOR one needs the thread's entity context — see the entity-anchored
    test below, which is the path the orchestrator actually uses.
    """
    hits = await seeded.recall(FRIDAY_QUERY, user_id="alice", k=4)
    assert hits, "semantic recall returned nothing"
    ranked = [m["text"] for m, _ in hits]
    assert any("DRAM" in t or "displaced" in t for t in ranked), (
        f"missed the supply-chain memories entirely: {ranked}"
    )


@pytest.mark.asyncio
async def test_entity_anchored_recall_resolves_the_anaphora(seeded):
    """The actual demo: anchoring on the thread's entities pins the right context.

    Friday's "what else is exposed to that supply chain?" + the session's entities
    (Fujian Jinhua, UMC, Micron) must recall the SEMICONDUCTOR facts and exclude
    the unrelated shipping one — which bare-query recall can't guarantee.
    """
    hits = await seeded.recall(
        FRIDAY_QUERY, user_id="alice", k=4, entity_names=["Fujian Jinhua", "UMC", "Micron"]
    )
    assert hits, "entity-anchored recall returned nothing"
    ranked = [m["text"] for m, _ in hits]

    assert any("DRAM" in t or "displaced" in t for t in ranked), ranked
    assert not any("Maersk" in t for t in ranked), (
        f"the shipping fact should be excluded by the semiconductor entity anchor: {ranked}"
    )


@pytest.mark.asyncio
async def test_recall_is_scoped_to_the_user_on_real_redis(seeded):
    """Bob must not see Alice's memories even through the shared vector index."""
    assert await seeded.recall(FRIDAY_QUERY, user_id="bob", k=4) == []


@pytest.mark.asyncio
async def test_entity_filtered_recall(seeded):
    """Recall filtered by entity name returns only memories about that entity."""
    hits = await seeded.recall(FRIDAY_QUERY, user_id="alice", k=4, entity_names=["Maersk"])
    assert hits, "entity-filtered recall returned nothing"
    assert all("maersk" in m["entity_names"] for m, _ in hits)


@pytest.mark.asyncio
async def test_forget_removes_from_the_index(seeded):
    """Deleting a memory drops it from Redis, not just SQLite."""
    from src.common import redis_client

    before = await seeded.recall(FRIDAY_QUERY, user_id="alice", k=4)
    victim = next(m for m, _ in before if "Maersk" in m["text"])
    assert seeded.forget(victim["memory_id"], "alice") is True

    keys = redis_client.get_redis().keys("mem:*")
    assert not any(victim["memory_id"] in k for k in keys), (
        "memory left in the Redis index after delete"
    )
