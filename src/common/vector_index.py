"""Redis vector/hybrid index over the saved entity graph.

**SQLite stays the system of record. This is a derived index.** `saved_entities`
lives on the Render persistent disk and is what `graph_save_entity` and
`POST /api/knowledge/entities` write through. Losing Redis loses *speed and
semantics*, never data — every caller falls back to the lexical path.

Why it exists: today "find companies like this one" (the customer's #1 ask) is
token overlap, so a target of *Fujian Jinhua* (a DRAM fab) ranks **Jinhua Group
Holdings** — a real-estate developer — above **SMIC**, an actual foundry, purely
because it shares the token "jinhua". Vector recall gets that right.

Shape of a query: **Redis recalls, Python re-ranks.** We ask Redis for ~20
candidates by hybrid score (vector KNN + BM25 + tag filters), then
`src/common/similarity.py` re-ranks *those* with the existing `_lexical_pair` so
every result keeps its explainable `basis`. A bare cosine float is not an answer
an analyst can put in a brief.

Requires the Redis **Query Engine** (`FT.*`), i.e. a real Redis 8 — Render's
Valkey keyvalue has no modules. Everything here checks `is_available()` and
returns None/False rather than raising, so on Valkey the app simply stays lexical.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from typing import Any

from src.common.config import config

logger = logging.getLogger(__name__)

ENTITY_INDEX_NAME = "emissary_entities"
ENTITY_PREFIX = "ent:"

# Bump when the field layout changes; a mismatch forces an explicit reindex
# instead of silently querying an index built for a different shape.
SCHEMA_VERSION = 1

_META_KEY = "emissary:index:meta"
# Durable retry queue: ids whose index write hasn't landed yet (Voyage down,
# Redis restarting, no event loop). Drained on startup and by an admin endpoint.
_DIRTY_SET = "emissary:ent:dirty"

# Recall width before Python re-ranks. Wider than top_k so the trait bonuses in
# _lexical_pair have something to reorder.
DEFAULT_RECALL = 20

_index = None
_index_ready = False
_meta_error = ""


# --- Schema -----------------------------------------------------------------


def _schema_dict(dims: int) -> dict:
    return {
        "index": {
            "name": ENTITY_INDEX_NAME,
            "prefix": ENTITY_PREFIX,
            "storage_type": "json",
        },
        "fields": [
            {"name": "entity_id", "type": "tag"},
            {"name": "name", "type": "text"},
            # The BM25 + embedding source: one flat string per entity (name, type,
            # country, aliases, identifiers, notes) built by similarity.entity_text.
            # Indexing and embedding the SAME text keeps the two halves of a hybrid
            # query consistent.
            {"name": "search_text", "type": "text"},
            {"name": "entity_type", "type": "tag"},
            {"name": "country", "type": "tag"},
            # sha256(search_text): lets a re-save of an unchanged entity skip the
            # embedding call entirely. The agent re-saves the same entities on
            # every run, so this is most of the traffic — and most of the cost.
            {"name": "text_hash", "type": "tag"},
            {"name": "updated_at", "type": "text"},
            {
                "name": "embedding",
                "type": "vector",
                "attrs": {
                    "dims": dims,
                    "algorithm": "hnsw",
                    "distance_metric": "cosine",
                    "datatype": "float32",
                    "m": 16,
                    "ef_construction": 200,
                },
            },
        ],
    }


def text_hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _key(entity_id: str) -> str:
    return f"{ENTITY_PREFIX}{entity_id}"


# --- Availability + index lifecycle -----------------------------------------


def is_available() -> bool:
    """True when a vector query can actually run: Redis + FT.* + embeddings."""
    if _meta_error:
        return False
    try:
        from src.common.embeddings import embeddings_enabled
        from src.common.redis_client import capabilities

        return capabilities().search and embeddings_enabled()
    except Exception:  # noqa: BLE001
        return False


def _check_meta(client) -> bool:
    """Refuse to use an index built for a different model/dims/schema.

    Querying a stale-dim HNSW index is the classic silent-garbage failure: it
    doesn't error, it just returns nonsense neighbours. So a mismatch disables the
    index (loudly) and requires an explicit reindex.
    """
    global _meta_error
    want = {
        "model": config.embedding_model,
        "dims": config.embedding_dims,
        "schema_v": SCHEMA_VERSION,
    }
    try:
        raw = client.get(_META_KEY)
    except Exception:  # noqa: BLE001
        return True  # can't read meta — don't block on it

    if not raw:
        try:
            client.set(_META_KEY, json.dumps(want))
        except Exception:  # noqa: BLE001
            pass
        return True

    try:
        have = json.loads(raw)
    except Exception:  # noqa: BLE001
        return True

    if have != want:
        _meta_error = (
            f"index built for {have} but config is {want} — refusing to query it. "
            f"Run `python -m scripts.reindex_entities --force` to rebuild."
        )
        logger.error("Entity vector index DISABLED: %s", _meta_error)
        return False
    return True


def get_index():
    """The RedisVL SearchIndex, or None. Creates it on first use (idempotent)."""
    global _index, _index_ready
    if _index_ready:
        return _index
    if not is_available():
        return None

    try:
        from redisvl.index import SearchIndex

        from src.common.redis_client import get_redis

        client = get_redis()
        if client is None:
            return None
        if not _check_meta(client):
            return None

        index = SearchIndex.from_dict(_schema_dict(config.embedding_dims), redis_client=client)
        # create() is a no-op when the index already exists (overwrite=False).
        index.create(overwrite=False)
        _index = index
        _index_ready = True
        logger.info(
            "Entity vector index ready: %s (dims=%d)", ENTITY_INDEX_NAME, config.embedding_dims
        )
        return _index
    except Exception as exc:  # noqa: BLE001
        logger.warning("Entity vector index unavailable (staying lexical): %s", exc)
        return None


def ensure_index() -> bool:
    """Create the index if absent. Safe to call on every boot."""
    return get_index() is not None


# --- Writes -----------------------------------------------------------------


def _existing_hash(client, entity_id: str) -> str | None:
    """The indexed text_hash for an entity, without fetching its 1536-float vector.

    Returns None when absent/unreadable, which just means "re-embed it".
    """
    try:
        raw = client.execute_command("JSON.GET", _key(entity_id), "$.text_hash")
        if not raw:
            return None
        # redis-py registers a response callback for JSON.GET, so with
        # decode_responses=True this already comes back as a parsed list — not the
        # raw JSON string. Handle both rather than assuming: json.loads() on a list
        # raises, and that silently disabled the skip (re-embedding everything on
        # every pass) until it was measured.
        parsed = json.loads(raw) if isinstance(raw, (str, bytes)) else raw
        if isinstance(parsed, list):
            return parsed[0] if parsed else None
        return parsed if isinstance(parsed, str) else None
    except Exception as exc:  # noqa: BLE001
        logger.debug("Could not read text_hash for %s (will re-embed): %s", entity_id, exc)
        return None


async def index_entity(entity: dict[str, Any]) -> bool:
    """Embed + upsert one entity into the index. Never raises."""
    index = get_index()
    if index is None:
        return False

    from src.common.embeddings import embed
    from src.common.similarity import entity_text

    entity_id = entity.get("entity_id")
    if not entity_id:
        return False

    search_text = entity_text(entity)
    if not search_text.strip():
        return False
    digest = text_hash(search_text)

    try:
        from src.common.redis_client import get_redis

        client = get_redis()
        # Unchanged text => the stored vector is still correct. Skip the API call.
        if client is not None and _existing_hash(client, entity_id) == digest:
            return True

        vector = await embed(search_text)
        if vector is None:  # embeddings off/failing — the dirty set retries later
            return False

        index.load(
            [
                {
                    "entity_id": entity_id,
                    "name": entity.get("name") or "",
                    "search_text": search_text,
                    "entity_type": entity.get("entity_type") or "",
                    "country": entity.get("country") or "",
                    "text_hash": digest,
                    "updated_at": entity.get("updated_at") or "",
                    "embedding": vector,
                }
            ],
            keys=[_key(entity_id)],
        )
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("Indexing entity %s failed: %s", entity_id, exc)
        return False


def remove_entity(entity_id: str) -> bool:
    """Drop an entity from the index. Never raises."""
    try:
        from src.common.redis_client import get_redis

        client = get_redis()
        if client is None:
            return False
        client.delete(_key(entity_id))
        client.srem(_DIRTY_SET, entity_id)
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("Removing entity %s from index failed: %s", entity_id, exc)
        return False


def enqueue_reindex(entity: dict[str, Any]) -> None:
    """Schedule an index write. **Never raises, never blocks.**

    Called at the very end of `knowledge_store.upsert_entity`, *after* the SQLite
    commit. It must not be able to fail a write to the system of record: embedding
    is a network call, and a Voyage timeout must never mean "saving an entity
    failed". So the row lands first, then the index catches up.

    Two mechanisms: a fire-and-forget task for the fast path, and a durable dirty
    set for everything that path can't cover (no running loop in a CLI/sync
    context, Voyage down, Redis restarting).
    """
    entity_id = entity.get("entity_id")
    if not entity_id:
        return
    try:
        from src.common.redis_client import get_redis

        client = get_redis()
        if client is not None:
            client.sadd(_DIRTY_SET, entity_id)
    except Exception as exc:  # noqa: BLE001
        logger.debug("Could not mark %s dirty (non-fatal): %s", entity_id, exc)

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return  # sync context (CLI, tests) — drain_dirty() will pick it up
    try:
        loop.create_task(_reindex_and_clear(entity))
    except Exception as exc:  # noqa: BLE001
        logger.debug("Could not schedule reindex for %s (non-fatal): %s", entity_id, exc)


def enqueue_removal(entity_id: str) -> None:
    """Remove from the index, best-effort. Never raises."""
    try:
        remove_entity(entity_id)
    except Exception:  # noqa: BLE001
        pass


async def _reindex_and_clear(entity: dict[str, Any]) -> None:
    if await index_entity(entity):
        try:
            from src.common.redis_client import get_redis

            client = get_redis()
            if client is not None:
                client.srem(_DIRTY_SET, entity["entity_id"])
        except Exception:  # noqa: BLE001
            pass


async def drain_dirty(limit: int = 500) -> dict[str, int]:
    """Index everything still marked dirty. Returns {indexed, failed, skipped}."""
    out = {"indexed": 0, "failed": 0, "skipped": 0}
    if not is_available():
        out["skipped"] = 1
        return out
    try:
        from src.common.redis_client import get_redis

        client = get_redis()
        if client is None:
            return out
        ids = list(client.smembers(_DIRTY_SET))[:limit]
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not read the dirty set: %s", exc)
        return out

    if not ids:
        return out

    from src.common import knowledge_store as ks

    for entity_id in ids:
        entity = ks.get_entity(entity_id)
        if entity is None:  # deleted since being marked
            remove_entity(entity_id)
            continue
        if await index_entity(entity):
            out["indexed"] += 1
            try:
                client.srem(_DIRTY_SET, entity_id)
            except Exception:  # noqa: BLE001
                pass
        else:
            out["failed"] += 1
    logger.info("Drained entity index dirty set: %s", out)
    return out


# --- Reads ------------------------------------------------------------------


async def search_entity_ids(
    query: str | None = None,
    *,
    vector_text: str | None = None,
    entity_type: str | None = None,
    country: str | None = None,
    top_k: int = DEFAULT_RECALL,
) -> list[tuple[str, float]] | None:
    """Recall entity ids by hybrid search: ``[(entity_id, semantic_score), ...]``.

    Returns **None** when unavailable — the caller must then fall back to the
    lexical path. An empty list means "searched, found nothing", which is a
    different answer and callers should treat it as such.

    Only ids + scores come back: the entity itself is hydrated from SQLite by the
    caller, so a result can never be stale relative to the system of record.
    """
    index = get_index()
    if index is None:
        return None

    text = (query or vector_text or "").strip()
    if not text:
        return None

    from src.common.embeddings import embed

    vector = await embed(vector_text or text)
    if vector is None:
        return None

    try:
        from redisvl.query import VectorQuery
        from redisvl.query.filter import Tag

        flt = None
        if entity_type:
            flt = Tag("entity_type") == entity_type
        if country:
            country_filter = Tag("country") == country
            flt = (flt & country_filter) if flt is not None else country_filter

        # KNN over FT.SEARCH — deliberately NOT RedisVL's HybridQuery, which
        # compiles to FT.HYBRID: that command doesn't exist before Redis 8.4 (8.2
        # answers `unknown command 'FT.HYBRID'`) and RedisVL itself flags it as
        # experimental/removable. We lose nothing by skipping it: the lexical
        # signal comes from re-ranking with _lexical_pair in similarity.py, and
        # BM25 was only ever recall diversity. This works on any Redis 8.
        q = VectorQuery(
            vector=vector,
            vector_field_name="embedding",
            filter_expression=flt,
            num_results=top_k,
            return_fields=["entity_id"],
        )
        results = index.query(q)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Entity vector search failed (falling back to lexical): %s", exc)
        return None

    out: list[tuple[str, float]] = []
    for r in results:
        entity_id = r.get("entity_id")
        if not entity_id:
            continue
        # Cosine distance -> similarity. Clamped: HNSW is approximate and float
        # error can push this a hair outside [0, 1], which would look like a
        # nonsense score in an analyst-facing basis.
        try:
            distance = float(r.get("vector_distance", 1.0))
        except (TypeError, ValueError):
            distance = 1.0
        out.append((entity_id, max(0.0, min(1.0, 1.0 - distance))))
    return out


# --- Maintenance ------------------------------------------------------------


async def backfill(entities: list[dict[str, Any]], batch_size: int = 64) -> dict[str, int]:
    """Index a list of entities. Returns {indexed, skipped, errors}.

    Batched because Voyage bills per call and its free tier is ~3 requests/minute —
    a per-entity loop would take hours on a real graph.
    """
    out = {"indexed": 0, "skipped": 0, "errors": 0}
    if not is_available():
        logger.warning("Backfill skipped: vector index unavailable (need Redis 8 + a Voyage key).")
        out["skipped"] = len(entities)
        return out

    from src.common.embeddings import embed_many
    from src.common.redis_client import get_redis
    from src.common.similarity import entity_text

    index = get_index()
    client = get_redis()
    if index is None or client is None:
        out["skipped"] = len(entities)
        return out

    pending: list[tuple[dict, str, str]] = []
    for entity in entities:
        if not entity.get("entity_id"):
            out["errors"] += 1
            continue
        search_text = entity_text(entity)
        if not search_text.strip():
            out["skipped"] += 1
            continue
        digest = text_hash(search_text)
        if _existing_hash(client, entity["entity_id"]) == digest:
            out["skipped"] += 1  # unchanged — no embedding spend
            continue
        pending.append((entity, search_text, digest))

    for i in range(0, len(pending), batch_size):
        chunk = pending[i : i + batch_size]
        vectors = await embed_many([t for _, t, _ in chunk])
        if vectors is None:
            out["errors"] += len(chunk)
            continue
        docs = []
        keys = []
        for (entity, search_text, digest), vector in zip(chunk, vectors):
            docs.append(
                {
                    "entity_id": entity["entity_id"],
                    "name": entity.get("name") or "",
                    "search_text": search_text,
                    "entity_type": entity.get("entity_type") or "",
                    "country": entity.get("country") or "",
                    "text_hash": digest,
                    "updated_at": entity.get("updated_at") or "",
                    "embedding": vector,
                }
            )
            keys.append(_key(entity["entity_id"]))
        try:
            index.load(docs, keys=keys)
            out["indexed"] += len(docs)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Backfill batch failed: %s", exc)
            out["errors"] += len(docs)

    return out


def drop_index() -> bool:
    """Delete the index and its docs. For --force reindex / ops."""
    global _index, _index_ready, _meta_error
    try:
        from src.common.redis_client import get_redis

        client = get_redis()
        if client is None:
            return False
        try:
            client.execute_command("FT.DROPINDEX", ENTITY_INDEX_NAME, "DD")
        except Exception:  # noqa: BLE001 — not existing is fine
            pass
        client.delete(_META_KEY)
        _index = None
        _index_ready = False
        _meta_error = ""
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("Dropping the entity index failed: %s", exc)
        return False


def status() -> dict[str, Any]:
    """Health view of the index."""
    info: dict[str, Any] = {
        "available": is_available(),
        "index": ENTITY_INDEX_NAME,
        "reason": _meta_error,
    }
    index = get_index()
    if index is not None:
        try:
            info["docs"] = int(index.info().get("num_docs", 0))
        except Exception:  # noqa: BLE001
            pass
    return info


def reset_for_tests() -> None:
    global _index, _index_ready, _meta_error
    _index = None
    _index_ready = False
    _meta_error = ""
