"""The embeddings seam must be OFF-and-honest, never ON-and-lying.

The failure this module exists to prevent already shipped once: the wargame's
`HashEmbedder` derived vectors from SHA-256 when VOYAGE_API_KEY was unset (the
prod state), so retrieval returned confidently-ranked, meaningless results with
nothing in the response to indicate it. These tests pin the opposite behaviour:

  * no key            -> get_vectorizer() is None, embed() is None, nothing raises
  * Voyage failing    -> None (degrade), never an exception into a request path
  * repeated failures -> circuit breaker opens so we stop adding 30s of timeout
  * dims != response  -> HARD disabled, because writing mis-shaped vectors into a
                         vector index is unrecoverable without a migration

No test here touches the network: the vectorizer is injected via the documented
test seam, and the Voyage HTTP layer is exercised against a stubbed transport.
"""

from __future__ import annotations

import pytest

from src.common import embeddings


@pytest.fixture(autouse=True)
def _reset():
    embeddings.reset_for_tests()
    yield
    embeddings.reset_for_tests()


class _FakeVectorizer:
    """Stands in for a RedisVL vectorizer. Tests only — never a prod fallback."""

    def __init__(self, dims: int = 1024, fail: bool = False, cache=None):
        self.dims = dims
        self.cache = cache
        self.fail = fail
        self.calls = 0

    async def aembed_many(self, texts):
        self.calls += 1
        if self.fail:
            raise RuntimeError("voyage exploded")
        return [[0.1] * self.dims for _ in texts]


# --- The absent-key path (what CI and prod run today) ------------------------


def test_no_api_key_means_no_vectorizer(monkeypatch):
    monkeypatch.setattr(embeddings.config, "voyage_api_key", "", raising=False)

    assert embeddings.get_vectorizer() is None
    assert embeddings.embeddings_enabled() is False


@pytest.mark.asyncio
async def test_no_api_key_embed_returns_none_and_does_not_raise(monkeypatch):
    monkeypatch.setattr(embeddings.config, "voyage_api_key", "", raising=False)

    # None, NOT a zero vector and NOT an exception. A caller must be able to tell
    # "no answer" from "an answer".
    assert await embeddings.embed("Fujian Jinhua") is None
    assert await embeddings.embed_many(["a", "b"]) is None


def test_status_explains_why_it_is_off(monkeypatch):
    monkeypatch.setattr(embeddings.config, "voyage_api_key", "", raising=False)

    status = embeddings.embeddings_status()
    assert status["enabled"] is False
    assert "VOYAGE_API_KEY" in status["reason"]
    assert status["dims"] == 0


def test_there_is_no_stub_embedder():
    """Guard against re-introducing the HashEmbedder mistake.

    If someone adds a hash/random/zero fallback to this module, this fails.
    """
    banned = ("hash", "sha256", "random", "fake", "stub", "dummy")
    public = [n for n in dir(embeddings) if not n.startswith("_")]
    offenders = [n for n in public if any(b in n.lower() for b in banned)]
    # `set_vectorizer_for_tests` is the one sanctioned injection point.
    assert offenders == [], f"no stand-in embedder may exist in this module: {offenders}"


# --- The happy path ----------------------------------------------------------


@pytest.mark.asyncio
async def test_embed_returns_a_vector_when_available():
    embeddings.set_vectorizer_for_tests(_FakeVectorizer(dims=1024))

    vec = await embeddings.embed("Fujian Jinhua")
    assert vec is not None
    assert len(vec) == 1024

    batch = await embeddings.embed_many(["a", "b", "c"])
    assert batch is not None and len(batch) == 3


@pytest.mark.asyncio
async def test_empty_input_is_not_an_api_call():
    v = _FakeVectorizer()
    embeddings.set_vectorizer_for_tests(v)

    assert await embeddings.embed("") is None
    assert await embeddings.embed("   ") is None
    assert await embeddings.embed_many([]) == []
    assert v.calls == 0  # never spend a Voyage call on nothing


# --- Failure handling --------------------------------------------------------


@pytest.mark.asyncio
async def test_voyage_failure_degrades_instead_of_raising():
    embeddings.set_vectorizer_for_tests(_FakeVectorizer(fail=True))

    assert await embeddings.embed("anything") is None  # must not raise


@pytest.mark.asyncio
async def test_circuit_breaker_opens_after_repeated_failures(monkeypatch):
    # A key must be present: this models "Voyage is down", which is the only way
    # the breaker can ever open (with no key we never call Voyage at all).
    monkeypatch.setattr(embeddings.config, "voyage_api_key", "test-key", raising=False)
    v = _FakeVectorizer(fail=True)
    embeddings.set_vectorizer_for_tests(v)

    for _ in range(embeddings._BREAKER_THRESHOLD):
        assert await embeddings.embed_many(["x"]) is None

    calls_at_trip = v.calls
    assert embeddings._breaker_is_open() is True

    # Breaker open => we stop calling Voyage entirely rather than eating a 30s
    # timeout on every request.
    assert await embeddings.embed_many(["x"]) is None
    assert v.calls == calls_at_trip, "breaker must stop further Voyage calls"

    status = embeddings.embeddings_status()
    assert status["enabled"] is False
    assert "circuit breaker" in status["reason"]


@pytest.mark.asyncio
async def test_success_resets_the_failure_count():
    v = _FakeVectorizer(fail=True)
    embeddings.set_vectorizer_for_tests(v)
    await embeddings.embed_many(["x"])
    assert embeddings._consecutive_failures == 1

    v.fail = False
    await embeddings.embed_many(["x"])
    assert embeddings._consecutive_failures == 0


def test_vectorizer_init_failure_does_not_deadlock(monkeypatch):
    """A Voyage failure during construction must return, not hang.

    get_vectorizer() calls _record_failure() from inside its own lock, and
    _record_failure() takes that lock again. With a non-reentrant threading.Lock
    that is a same-thread self-deadlock: the process freezes, forever, on the
    exact path a Voyage 429/outage takes. Hence RLock.

    If this regresses, this test HANGS rather than fails — which is itself the
    signature of the bug.
    """
    import threading

    assert isinstance(embeddings._lock, type(threading.RLock())), (
        "embeddings._lock must be reentrant — get_vectorizer() re-enters it via "
        "_record_failure()/_record_success()"
    )

    monkeypatch.setattr(embeddings.config, "voyage_api_key", "test-key", raising=False)
    monkeypatch.setattr(embeddings, "_make_cache", lambda: None)

    class _Boom:
        def __init__(self, **kwargs):
            raise RuntimeError("429 Too Many Requests")

    import redisvl.utils.vectorize as rv

    monkeypatch.setattr(rv, "CustomTextVectorizer", _Boom)

    # Returns None (degrades) rather than deadlocking.
    assert embeddings.get_vectorizer() is None
    assert embeddings._consecutive_failures == 1

    # And the same on the success path, which also re-enters the lock via
    # _record_success() once a failure has been recorded.
    class _Ok:
        def __init__(self, **kwargs):
            self.dims = embeddings.config.embedding_dims
            self.cache = None

    monkeypatch.setattr(rv, "CustomTextVectorizer", _Ok)
    assert embeddings.get_vectorizer() is not None
    assert embeddings._consecutive_failures == 0


# --- The dimension guard (the 1536-vs-1024 bug, as a test) -------------------


def test_dimension_mismatch_hard_disables(monkeypatch):
    """A model that disagrees with EMBEDDING_DIMS must disable, not proceed.

    Writing 1024-d vectors into a 1536-d index is unrecoverable without a
    migration, so this is a config error we refuse rather than absorb.

    Not hypothetical: the repo's defaults paired voyage-3 with 1536 dims, which is
    impossible (verified live: voyage-3 -> 1024, voyage-large-2 -> 1536). It stayed
    hidden only because the deployed EMBEDDING_MODEL overrides to voyage-large-2,
    whose 1536 does match the agent_memory column.
    """
    monkeypatch.setattr(embeddings.config, "voyage_api_key", "test-key", raising=False)
    monkeypatch.setattr(embeddings.config, "embedding_dims", 1536, raising=False)

    class _Ctor:
        def __init__(self, **kwargs):
            self.dims = 1024  # what voyage-3 really returns
            self.cache = None

    monkeypatch.setattr(embeddings, "_make_cache", lambda: None)
    import redisvl.utils.vectorize as rv

    monkeypatch.setattr(rv, "CustomTextVectorizer", _Ctor)

    assert embeddings.get_vectorizer() is None

    status = embeddings.embeddings_status()
    assert status["enabled"] is False
    assert "dimension mismatch" in status["reason"]
    assert "1024" in status["reason"] and "1536" in status["reason"]


def test_matching_dimensions_enable(monkeypatch):
    monkeypatch.setattr(embeddings.config, "voyage_api_key", "test-key", raising=False)
    monkeypatch.setattr(embeddings.config, "embedding_dims", 1024, raising=False)

    class _Ctor:
        def __init__(self, **kwargs):
            self.dims = 1024
            self.cache = None

    monkeypatch.setattr(embeddings, "_make_cache", lambda: None)
    import redisvl.utils.vectorize as rv

    monkeypatch.setattr(rv, "CustomTextVectorizer", _Ctor)

    assert embeddings.get_vectorizer() is not None
    assert embeddings.embeddings_status()["enabled"] is True


# --- The Voyage HTTP layer (stubbed transport, no network) -------------------


def test_response_is_ordered_by_index_not_arrival(monkeypatch):
    """Voyage returns an explicit index; honour it rather than trusting order.

    If this regressed, embeddings would be silently attached to the WRONG text —
    the kind of bug that produces plausible, confidently-wrong neighbours.
    """
    import httpx

    def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "data": [
                    {"index": 2, "embedding": [3.0]},
                    {"index": 0, "embedding": [1.0]},
                    {"index": 1, "embedding": [2.0]},
                ]
            },
        )

    transport = httpx.MockTransport(_handler)
    real_client = httpx.Client

    monkeypatch.setattr(
        httpx,
        "Client",
        lambda **kw: real_client(
            transport=transport, **{k: v for k, v in kw.items() if k != "transport"}
        ),
    )
    monkeypatch.setattr(embeddings.config, "voyage_api_key", "test-key", raising=False)

    vectors = embeddings._embed_many_sync(["first", "second", "third"])
    assert vectors == [[1.0], [2.0], [3.0]]
