"""Semantic pre-warm cache (Phase 6): the entity-signature safety gate.

The single most important behaviour: an entity swap ("…owns Nuctech…" vs
"…owns Hikvision…") must NEVER auto-replay, even though real embeddings score it
~0.897 — dangerously close to a true paraphrase (0.960). A distance threshold
alone can't separate them; the entity-signature gate is what does.

The entity-signature logic is deterministic, so it's tested directly. The
vector-scoring path is tested with an injected stub vectorizer (controllable
distances) — the real-vector spread is verified in the integration suite.
"""

from __future__ import annotations

import pytest

from src.common import embeddings, semantic_cache
from src.common.semantic_cache import entity_signature


@pytest.fixture(autouse=True)
def _reset():
    embeddings.reset_for_tests()
    yield
    embeddings.reset_for_tests()


# --- The entity signature (deterministic) ------------------------------------


def test_entity_signature_distinguishes_the_swap():
    a = entity_signature("Who ultimately owns Nuctech, and what are its sanctions exposures?")
    b = entity_signature("Who ultimately owns Hikvision, and what are its sanctions exposures?")
    assert "nuctech" in a
    assert "hikvision" in b
    assert a != b  # the whole point — the swap changes the signature


def test_entity_signature_is_stable_across_paraphrase():
    a = entity_signature(
        "What happens to global semiconductor supply if we sanction Fujian Jinhua?"
    )
    b = entity_signature("If Fujian Jinhua were sanctioned, how would chip supply be affected?")
    assert "fujian" in a and "jinhua" in a
    assert {"fujian", "jinhua"} <= b
    # A genuine paraphrase keeps the same entities.
    assert a & b == {"fujian", "jinhua"} or {"fujian", "jinhua"} <= (a & b)


def test_entity_signature_catches_acronyms_and_years():
    sig = entity_signature("How exposed is the drone supply chain to a DJI export ban after 2019?")
    assert "dji" in sig  # all-caps acronym
    assert "2019" in sig
    # Question words / common capitalized starters are excluded.
    assert "how" not in sig


def test_entity_signature_excludes_question_words():
    sig = entity_signature("What Who How Map Which")
    assert sig == frozenset()


# --- Banding + the auto-replay gate ------------------------------------------


class _StubVectorizer:
    """Returns a canned vector per text so cosine distances are controllable.

    Unknown texts (e.g. the demo queries that `_warmed_queries()` always includes)
    get a fixed orthogonal vector, so they land far from the texts under test
    rather than raising.
    """

    def __init__(self, table):
        self._table = table
        self.dims = len(next(iter(table.values())))
        self._default = [0.0] + [1.0] * (self.dims - 1)  # orthogonal to [1, 0, ...]
        self.cache = None

    def _one(self, t):
        return self._table.get(t, self._default)

    async def aembed_many(self, texts):
        return [self._one(t) for t in texts]

    async def aembed(self, text):
        return self._one(text)

    def embed_many(self, texts):
        return [self._one(t) for t in texts]

    def embed(self, text):
        return self._one(text)


NUCTECH = "Who ultimately owns Nuctech, and what are its sanctions exposures?"
HIKVISION = "Who ultimately owns Hikvision, and what are its sanctions exposures?"
NUCTECH_REWORD = "What sanctions exposures does Nuctech have, and who ultimately owns it?"


def _inject(table):
    embeddings.set_vectorizer_for_tests(_StubVectorizer(table))


@pytest.mark.asyncio
async def test_exact_match_short_circuits():
    _inject({NUCTECH: [1.0, 0.0]})
    m = await semantic_cache.match_query(NUCTECH, [NUCTECH])
    assert m.band == "exact"
    assert m.query == NUCTECH


@pytest.mark.asyncio
async def test_entity_swap_never_auto_replays(monkeypatch):
    """The core safety test: near-identical vectors, DIFFERENT entities.

    Even with replay enabled and a similarity that would clear the auto-replay
    threshold, a differing entity signature must demote it to 'suggest'.
    """
    monkeypatch.setattr(semantic_cache, "REPLAY_ENABLED", True)
    # Make the two questions ~0.99 similar as vectors (the adversarial case).
    _inject({HIKVISION: [1.0, 0.02], NUCTECH: [1.0, 0.0]})

    m = await semantic_cache.match_query(HIKVISION, [NUCTECH])
    assert m.similarity >= semantic_cache.AUTO_REPLAY_MIN_SIMILARITY  # vectors say "replay"
    assert m.entity_signature_match is False  # but the entities differ
    assert m.band == "suggest", "an entity swap must NEVER auto-replay"


@pytest.mark.asyncio
async def test_true_paraphrase_auto_replays_when_enabled(monkeypatch):
    """Same entity + high similarity + replay enabled → auto_replay."""
    monkeypatch.setattr(semantic_cache, "REPLAY_ENABLED", True)
    _inject({NUCTECH_REWORD: [1.0, 0.02], NUCTECH: [1.0, 0.0]})

    m = await semantic_cache.match_query(NUCTECH_REWORD, [NUCTECH])
    assert m.entity_signature_match is True
    assert m.band == "auto_replay"
    assert m.query == NUCTECH


@pytest.mark.asyncio
async def test_auto_replay_is_off_by_default(monkeypatch):
    """With replay disabled, even a perfect paraphrase is only a suggestion."""
    monkeypatch.setattr(semantic_cache, "REPLAY_ENABLED", False)
    _inject({NUCTECH_REWORD: [1.0, 0.02], NUCTECH: [1.0, 0.0]})

    m = await semantic_cache.match_query(NUCTECH_REWORD, [NUCTECH])
    assert m.band == "suggest"  # never auto_replay unless explicitly enabled


@pytest.mark.asyncio
async def test_unrelated_query_is_none(monkeypatch):
    monkeypatch.setattr(semantic_cache, "REPLAY_ENABLED", True)
    _inject({"totally unrelated question about weather": [0.0, 1.0], NUCTECH: [1.0, 0.0]})
    m = await semantic_cache.match_query("totally unrelated question about weather", [NUCTECH])
    assert m.band == "none"


# --- Lexical fallback (no embeddings) can never auto-replay -------------------


@pytest.mark.asyncio
async def test_lexical_fallback_is_suggest_at_most(monkeypatch):
    """With replay ENABLED but no embeddings, the Jaccard path still can't answer.

    This is the guarantee that a missing Voyage key can never turn into a silent
    wrong answer — degradation is to suggest-only.
    """
    monkeypatch.setattr(semantic_cache, "REPLAY_ENABLED", True)
    # No vectorizer injected → is_semantic_available() is False → lexical path.
    assert semantic_cache.is_semantic_available() is False

    m = await semantic_cache.match_query(NUCTECH_REWORD, [NUCTECH])
    assert m.backend == "lexical"
    assert m.band in ("suggest", "none")
    assert m.band != "auto_replay"


# --- Router auto-replay: disclosure + force_fresh ----------------------------


@pytest.mark.asyncio
async def test_router_auto_replay_discloses_and_records(monkeypatch):
    """An auto-replay must announce itself (event + replayed_from) before the answer."""
    import src.routers.orchestrator as orch
    from src.common.analyses import get_analysis_store

    _analyses = get_analysis_store()

    monkeypatch.setattr(semantic_cache, "REPLAY_ENABLED", True)
    _inject({NUCTECH_REWORD: [1.0, 0.02], NUCTECH: [1.0, 0.0]})

    # Warmed payload lives under the canonical (Nuctech) query.
    warmed = {"events": [], "result": {"executive_summary": "Nuctech ownership assessment"}}

    def _fake_get_cached(ns, **kw):
        if kw.get("registry"):
            return [NUCTECH]  # the warmed registry
        if orch._normalize_query(kw.get("q", "")) == orch._normalize_query(NUCTECH):
            return warmed
        return None  # no exact hit for the reworded query

    monkeypatch.setattr(orch, "_PREWARM", True)
    monkeypatch.setattr(orch, "get_cached", _fake_get_cached)

    _analyses["ar1"] = {
        "analysis_id": "ar1",
        "status": "running",
        "replayed_from": None,
        "notice": None,
        "progress": [],
        "events": [],
        "result": None,
    }
    await orch._run_analysis("ar1", NUCTECH_REWORD)

    entry = _analyses["ar1"]
    assert entry["result"]["executive_summary"] == "Nuctech ownership assessment"
    assert entry["replayed_from"]["query"] == NUCTECH
    # The disclosure event fired.
    assert any(e.get("type") == "replay_notice" for e in entry["events"])


@pytest.mark.asyncio
async def test_router_force_fresh_skips_auto_replay(monkeypatch):
    """force_fresh (the 'run the exact question' button) must bypass replay."""
    import src.routers.orchestrator as orch
    from src.common.analyses import get_analysis_store

    _analyses = get_analysis_store()

    monkeypatch.setattr(semantic_cache, "REPLAY_ENABLED", True)
    _inject({NUCTECH_REWORD: [1.0, 0.02], NUCTECH: [1.0, 0.0]})
    monkeypatch.setattr(orch, "_PREWARM", True)
    monkeypatch.setattr(
        orch,
        "get_cached",
        lambda ns, **kw: [NUCTECH] if kw.get("registry") else None,
    )
    # Orchestrator() would need an API key — force_fresh should reach the live
    # path and fail there, NOT replay. We assert it did not replay.
    monkeypatch.setattr(
        orch, "Orchestrator", lambda: (_ for _ in ()).throw(RuntimeError("live path reached"))
    )

    _analyses["ff1"] = {
        "analysis_id": "ff1",
        "status": "running",
        "replayed_from": None,
        "notice": None,
        "progress": [],
        "events": [],
        "result": None,
    }
    await orch._run_analysis("ff1", NUCTECH_REWORD, force_fresh=True)

    entry = _analyses["ff1"]
    assert entry["replayed_from"] is None, "force_fresh must not auto-replay"
    assert entry["status"] == "failed"  # it took the live path (which we stubbed to fail)
