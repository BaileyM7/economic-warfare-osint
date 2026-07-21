"""The single embeddings seam for the Emissary app.

Everything that needs a vector — semantic entity search, the semantic cache,
agent memory — comes through here, so there is one Voyage client, one model
constant, one dimension constant, and one place that decides whether embeddings
are available at all.

Three rules, in priority order. They are the whole point of this module:

1. **No key => no vectorizer. Never a stand-in.**
   ``get_vectorizer()`` returns ``None`` when ``VOYAGE_API_KEY`` is unset, and
   every caller must branch on that and take its existing lexical path. There is
   deliberately no hash/random/stub embedder. The wargame shipped one for months
   (``HashEmbedder``, deleted): with no key it derived vectors from SHA-256, so
   ``recall()`` ran cosine similarity over hashes and returned confidently-ranked,
   semantically meaningless memories with nothing in the response to say so.
   Silent garbage is worse than a hard failure — a caller can handle "off", but it
   cannot detect "lying".

2. **Never raise into a request path.**
   A Voyage outage, timeout, or 429 returns ``None``, not an exception. A circuit
   breaker then stops us adding retry latency to every subsequent request until it
   recovers.

3. **Trust the model, not the constant.**
   ``EMBEDDING_DIMS`` is *asserted against the live response*, never assumed. This
   repo already shipped a 1536-vs-1024 dimension lie that only stayed hidden
   because no real embedder ever ran (see alembic 0005). A mismatch disables
   embeddings loudly rather than writing mis-shaped vectors into an index.

Cheap by construction: an ``EmbeddingsCache`` (plain hashed KV — verified to work
on Valkey, so it does *not* need the Query Engine) means re-embedding identical
text costs nothing. That matters because the agent re-saves the same entities on
every run.

Note: `src/wargame_ai/memory/embeddings.py` has its own Voyage client by design —
the wargame is an optional extra (`uv sync --extra wargame`) that this module must
not import, and it deliberately never imports `src.*` either. Keep the two in sync
by hand; a fix here probably belongs there too.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any

from src.common.config import config

logger = logging.getLogger(__name__)

_ENDPOINT = "https://api.voyageai.com/v1/embeddings"
_TIMEOUT_SEC = 30.0

# Cache vectors for 30 days, keyed by (text, model). Embeddings for a given model
# are immutable, so the only reason to expire at all is to bound memory.
_CACHE_NAME = "emissary:embcache"
_CACHE_TTL_SEC = 30 * 24 * 3600

# Circuit breaker: after this many consecutive failures, stop calling Voyage for
# _BREAKER_COOLDOWN_SEC. Without it, a Voyage outage adds a 30s timeout to every
# single request that wants a vector.
_BREAKER_THRESHOLD = 5
_BREAKER_COOLDOWN_SEC = 60.0

# RLock, NOT Lock: get_vectorizer() calls _record_failure()/_record_success()
# from inside its own critical section, and both take this lock again. With a
# plain Lock that is a same-thread self-deadlock — it hangs the process forever,
# and only on the failure path (Voyage 429/outage during construction), i.e.
# exactly when you least want the app to freeze. Found by an integration run that
# tripped Voyage's rate limit.
_lock = threading.RLock()
_vectorizer: Any | None = None
_vectorizer_built = False
_disabled_reason = ""  # non-empty => hard-disabled (config error, not transient)
_consecutive_failures = 0
_breaker_open_until = 0.0


class EmbeddingsUnavailable(RuntimeError):
    """Raised only by callers that explicitly demand embeddings. Not raised here."""


# --- Voyage HTTP -------------------------------------------------------------


def _payload(texts: list[str], input_type: str) -> dict:
    return {"model": config.embedding_model, "input": texts, "input_type": input_type}


def _headers() -> dict:
    return {
        "Authorization": f"Bearer {config.voyage_api_key}",
        "Content-Type": "application/json",
    }


def _parse(data: dict) -> list[list[float]]:
    # Voyage returns data[] in request order, but it carries an explicit index —
    # sort by it rather than trusting order.
    items = sorted(data.get("data", []), key=lambda d: d.get("index", 0))
    return [item["embedding"] for item in items]


def _embed_many_sync(texts: list[str], input_type: str = "document") -> list[list[float]]:
    """Blocking Voyage call. Used for the one-time dimension probe at construction."""
    import httpx

    if not texts:
        return []
    with httpx.Client(timeout=_TIMEOUT_SEC) as client:
        resp = client.post(_ENDPOINT, json=_payload(texts, input_type), headers=_headers())
        resp.raise_for_status()
        return _parse(resp.json())


async def _aembed_many(texts: list[str], input_type: str = "document") -> list[list[float]]:
    """Async Voyage call — the path every request actually takes."""
    import httpx

    if not texts:
        return []
    async with httpx.AsyncClient(timeout=_TIMEOUT_SEC) as client:
        resp = await client.post(_ENDPOINT, json=_payload(texts, input_type), headers=_headers())
        resp.raise_for_status()
        return _parse(resp.json())


def _embed_one_sync(text: str) -> list[float]:
    vectors = _embed_many_sync([text])
    if not vectors:
        raise ValueError("Voyage returned no embedding")
    return vectors[0]


async def _aembed_one(text: str) -> list[float]:
    vectors = await _aembed_many([text])
    if not vectors:
        raise ValueError("Voyage returned no embedding")
    return vectors[0]


# --- Circuit breaker ---------------------------------------------------------


def _breaker_is_open() -> bool:
    return time.monotonic() < _breaker_open_until


def _record_failure(exc: Exception) -> None:
    global _consecutive_failures, _breaker_open_until
    with _lock:
        _consecutive_failures += 1
        if _consecutive_failures >= _BREAKER_THRESHOLD and not _breaker_is_open():
            _breaker_open_until = time.monotonic() + _BREAKER_COOLDOWN_SEC
            logger.error(
                "Embeddings circuit breaker OPEN after %d consecutive failures "
                "(last: %s). Pausing Voyage calls for %.0fs; callers degrade to lexical.",
                _consecutive_failures,
                exc,
                _BREAKER_COOLDOWN_SEC,
            )


def _record_success() -> None:
    global _consecutive_failures, _breaker_open_until
    if _consecutive_failures or _breaker_open_until:
        with _lock:
            _consecutive_failures = 0
            _breaker_open_until = 0.0


# --- Cache -------------------------------------------------------------------


def _make_cache():
    """An EmbeddingsCache on the shared Redis, or None.

    Gated on `ping`, NOT on `search`: the cache is plain hashed key/value, so it
    works on Valkey too. If Redis is missing or misbehaves this is None and the
    vectorizer simply doesn't cache — the cache being unavailable must never
    disable embeddings.
    """
    try:
        from src.common.redis_client import capabilities, get_redis

        if not capabilities().ping:
            return None
        client = get_redis()
        if client is None:
            return None

        from redisvl.extensions.cache.embeddings import EmbeddingsCache

        return EmbeddingsCache(name=_CACHE_NAME, ttl=_CACHE_TTL_SEC, redis_client=client)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Embeddings cache unavailable (continuing without it): %s", exc)
        return None


# --- Public API --------------------------------------------------------------


def get_vectorizer():
    """The shared RedisVL vectorizer, or ``None`` when embeddings are unavailable.

    ``None`` means: no API key, a config error (dimension mismatch), or the
    circuit breaker is open. Callers MUST handle it by degrading to lexical —
    never by substituting fake vectors.

    Built lazily and cached. Construction makes one real Voyage call (RedisVL's
    CustomTextVectorizer derives `dims` from a live response), which is exactly
    the dimension probe we want — so the constant is verified against the model
    rather than believed.
    """
    global _vectorizer, _vectorizer_built, _disabled_reason

    if _disabled_reason:  # hard-disabled; retrying won't help
        return None
    # Checked BEFORE the cached return: an open breaker means a vector cannot be
    # produced right now, so this must report None even though a vectorizer object
    # exists. Otherwise embeddings_enabled() would say True while embed() returns
    # None — the module would be lying about its own state.
    if _breaker_is_open():
        return None
    if _vectorizer_built:
        return _vectorizer
    if not config.voyage_api_key:
        return None

    with _lock:
        if _vectorizer_built or _disabled_reason:
            return _vectorizer

        try:
            from redisvl.utils.vectorize import CustomTextVectorizer

            vectorizer = CustomTextVectorizer(
                embed=_embed_one_sync,
                embed_many=_embed_many_sync,
                aembed=_aembed_one,
                aembed_many=_aembed_many,
                cache=_make_cache(),
            )
        except Exception as exc:  # noqa: BLE001 — network, auth, bad key...
            # Transient (Voyage down) or fatal (bad key) — we can't tell them
            # apart here, so let the breaker decide whether to keep trying.
            _record_failure(exc)
            logger.warning("Embeddings unavailable — could not init vectorizer: %s", exc)
            return None

        # Trust the model over the constant. A mismatch is a CONFIG error: it would
        # write mis-shaped vectors into an index whose width can't be changed
        # without a migration, so refuse rather than corrupt.
        if vectorizer.dims != config.embedding_dims:
            _disabled_reason = (
                f"dimension mismatch: {config.embedding_model} returns {vectorizer.dims} dims "
                f"but EMBEDDING_DIMS={config.embedding_dims}"
            )
            logger.error(
                "Embeddings DISABLED — %s. Set EMBEDDING_DIMS=%d (and migrate any vector "
                "column/index to match) before enabling embeddings.",
                _disabled_reason,
                vectorizer.dims,
            )
            return None

        _record_success()
        _vectorizer = vectorizer
        _vectorizer_built = True
        logger.info(
            "Embeddings ready: model=%s dims=%d cache=%s",
            config.embedding_model,
            vectorizer.dims,
            "redis" if vectorizer.cache else "none",
        )
        return _vectorizer


def embeddings_enabled() -> bool:
    """True when a vector can actually be produced right now."""
    return get_vectorizer() is not None


def embeddings_status() -> dict:
    """Health-endpoint view: is this on, and if not, why not."""
    enabled = embeddings_enabled()
    if enabled:
        reason = ""
    elif _disabled_reason:
        reason = _disabled_reason
    elif not config.voyage_api_key:
        reason = "VOYAGE_API_KEY not set — semantic features use their lexical fallback"
    elif _breaker_is_open():
        reason = "circuit breaker open after repeated Voyage failures"
    else:
        reason = "vectorizer unavailable"

    return {
        "enabled": enabled,
        "model": config.embedding_model if enabled else "",
        "dims": config.embedding_dims if enabled else 0,
        "cache": "redis" if (enabled and getattr(_vectorizer, "cache", None)) else "none",
        "reason": reason,
    }


async def embed(text: str) -> list[float] | None:
    """Embed one string. ``None`` on any failure — callers degrade, never crash."""
    if not text or not text.strip():
        return None
    vectors = await embed_many([text])
    return vectors[0] if vectors else None


async def embed_many(texts: list[str]) -> list[list[float]] | None:
    """Embed a batch. ``None`` (not a list of garbage) when unavailable or failing.

    Returns None — rather than partial/zero vectors — so a caller can never
    mistake a failure for a result.
    """
    if not texts:
        return []

    # None covers every unavailable case — no key, config error, breaker open.
    vectorizer = get_vectorizer()
    if vectorizer is None:
        return None

    try:
        vectors = await vectorizer.aembed_many(texts)
    except Exception as exc:  # noqa: BLE001 — timeout, 429, 5xx, transport...
        _record_failure(exc)
        logger.warning("Embedding call failed (degrading to lexical): %s", exc)
        return None

    _record_success()
    return vectors


# --- Test seam ---------------------------------------------------------------


def set_vectorizer_for_tests(vectorizer) -> None:
    """Inject a vectorizer (or None) without touching the network.

    The ONLY supported way to fake embeddings. Note this exists for *tests* — it
    is not a production fallback, and nothing in src/ may call it.
    """
    global _vectorizer, _vectorizer_built, _disabled_reason
    _vectorizer = vectorizer
    _vectorizer_built = vectorizer is not None
    _disabled_reason = ""
    reset_breaker_for_tests()


def reset_for_tests() -> None:
    """Drop all cached state so the next call re-probes."""
    global _vectorizer, _vectorizer_built, _disabled_reason
    _vectorizer = None
    _vectorizer_built = False
    _disabled_reason = ""
    reset_breaker_for_tests()


def reset_breaker_for_tests() -> None:
    global _consecutive_failures, _breaker_open_until
    _consecutive_failures = 0
    _breaker_open_until = 0.0
