"""Working memory — the server-side conversation thread (single seam).

Today Emissary is stateless: every ``POST /api/analyze`` mints a fresh id and
starts from zero, and ``POST /api/followup`` only works because the *browser*
posts the entire prior assessment back on every question. `Orchestrator`'s
docstring claims it "owns the conversation"; there has never been a conversation
object. This module is that object.

**Working memory** is session-scoped and short-lived: the turns of a thread, the
last plan, the last findings, the last assessment, the entities involved. It is
deliberately the *only* half implemented here — long-term memory (extracted facts
+ vector recall) is a later phase and needs embeddings and the Redis Query Engine.
Working memory needs neither: it is plain ``SET``/``GET``/``EXPIRE``, so it runs
on the Valkey keyvalue we already pay for.

Like `src/common/knowledge_store.py`, this is a **single seam**: the routers and
(later) the agent tools both go through these functions, so the two paths cannot
drift apart.

Storage is a cache, never a system of record:

  * Redis when ``REDIS_URL`` is set — survives restarts, shared across workers.
  * A process-local ``TTLCache`` otherwise — so local dev and CI work untouched,
    with the same semantics minus durability.
  * Any Redis failure degrades to "no memory" rather than raising. A thread that
    forgets is a bad session; a thread that 500s is a broken product.

Scoping: every read is checked against the caller's ``user_id``, which comes from
the auth token — never from a request body. A session belonging to another user
reads as absent.
"""

from __future__ import annotations

import hashlib
import json
import logging
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any

from cachetools import TTLCache

from src.common.types import Confidence

logger = logging.getLogger(__name__)

# A week: long enough for the Monday-question -> Friday-follow-up thread that is
# the whole point of having memory, short enough to bound Redis growth.
WM_TTL_SECONDS = 7 * 24 * 3600

_KEY_PREFIX = "emissary:wm:"

# Turns are capped so a long-lived thread can't grow without bound. Oldest go
# first — recent context is what a follow-up needs.
MAX_TURNS = 50

# An assessment carries full GDELT/Comtrade payloads. Refuse to store a document
# beyond this rather than let one session eat the instance; the caller still works,
# it just won't have hydrated context.
_MAX_DOC_BYTES = 4 * 1024 * 1024  # 4 MB

# Fallback store. Sized like src/common/analyses.py: bounded so a busy process
# can't OOM the 2 GB box. Process-local, so it does NOT survive a restart —
# that's the honest cost of running without Redis.
_FALLBACK_MAX_SESSIONS = 200
_fallback: TTLCache[str, dict[str, Any]] = TTLCache(
    maxsize=_FALLBACK_MAX_SESSIONS, ttl=WM_TTL_SECONDS
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class Turn:
    """One message in a thread."""

    role: str  # "user" | "assistant"
    text: str
    at: str = field(default_factory=_now)
    # Links a turn to the analysis it kicked off, so a follow-up can find the run
    # that produced the answer it is asking about.
    analysis_id: str | None = None


@dataclass
class WorkingMemory:
    """A session's short-term state."""

    session_id: str
    user_id: str
    turns: list[Turn] = field(default_factory=list)
    # The last decomposed research plan (steps/tools/depends_on).
    plan: list[dict[str, Any]] = field(default_factory=list)
    # Compacted tool findings from the last run.
    findings: dict[str, Any] = field(default_factory=dict)
    # The last ImpactAssessment.model_dump(mode="json"). This is the SAME shape the
    # browser currently posts back as `context`, which is what lets followup.py
    # hydrate from here without touching any of its prompt builders.
    assessment: dict[str, Any] | None = None
    # [{entity_id?, name, entity_type?, country?}] surfaced by the last run.
    entities: list[dict[str, Any]] = field(default_factory=list)
    context_type: str = "orchestrator"
    updated_at: str = field(default_factory=_now)

    def to_json(self) -> str:
        return json.dumps(asdict(self), default=str)

    @classmethod
    def from_dict(cls, doc: dict[str, Any]) -> WorkingMemory:
        turns = [Turn(**t) for t in doc.get("turns", [])]
        return cls(
            session_id=doc["session_id"],
            user_id=doc["user_id"],
            turns=turns,
            plan=doc.get("plan") or [],
            findings=doc.get("findings") or {},
            assessment=doc.get("assessment"),
            entities=doc.get("entities") or [],
            context_type=doc.get("context_type") or "orchestrator",
            updated_at=doc.get("updated_at") or _now(),
        )


# --- Storage ----------------------------------------------------------------


def _key(session_id: str) -> str:
    return f"{_KEY_PREFIX}{session_id}"


def _redis():
    """The shared Redis client, or None. Working memory needs no FT.*/JSON."""
    try:
        from src.common.redis_client import get_redis

        return get_redis()
    except Exception:  # noqa: BLE001
        return None


def backend_name() -> str:
    """ "redis" or "memory" — surfaced on /api/health so the mode is visible."""
    return "redis" if _redis() is not None else "memory"


def _load(session_id: str) -> dict[str, Any] | None:
    client = _redis()
    if client is None:
        return _fallback.get(session_id)
    try:
        raw = client.get(_key(session_id))
        return json.loads(raw) if raw else None
    except Exception as exc:  # noqa: BLE001
        logger.warning("Working memory read failed for %s (continuing): %s", session_id, exc)
        return None


def _save(wm: WorkingMemory) -> bool:
    """Persist with a refreshed TTL. False = not stored (never raises)."""
    wm.updated_at = _now()
    payload = wm.to_json()

    if len(payload.encode()) > _MAX_DOC_BYTES:
        logger.warning(
            "Working memory doc for %s is %d bytes (> %d cap) — not stored. The thread "
            "will fall back to client-supplied context.",
            wm.session_id,
            len(payload.encode()),
            _MAX_DOC_BYTES,
        )
        return False

    client = _redis()
    if client is None:
        _fallback[wm.session_id] = json.loads(payload)
        return True
    try:
        client.set(_key(wm.session_id), payload, ex=WM_TTL_SECONDS)
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("Working memory write failed for %s (continuing): %s", wm.session_id, exc)
        return False


# --- Public API -------------------------------------------------------------


def new_session(user_id: str) -> str:
    """Start a thread and return its id. Cheap — nothing is written until a turn."""
    session_id = uuid.uuid4().hex
    _save(WorkingMemory(session_id=session_id, user_id=user_id))
    return session_id


def get_working(session_id: str, user_id: str) -> WorkingMemory | None:
    """A session's memory, or None if absent/expired/not this user's.

    The ``user_id`` check is the isolation boundary. It is deliberately checked
    here rather than by callers so no route can forget it, and it must always be
    passed the value from the auth token — never one from a request body.
    """
    if not session_id:
        return None
    doc = _load(session_id)
    if not doc:
        return None
    if doc.get("user_id") != user_id:
        # Not an error path worth 403-ing: to the caller, someone else's session
        # simply doesn't exist. Logged because it may indicate a probe.
        logger.warning(
            "Working memory %s requested by %r but owned by %r — treating as absent.",
            session_id,
            user_id,
            doc.get("user_id"),
        )
        return None
    try:
        return WorkingMemory.from_dict(doc)
    except Exception as exc:  # noqa: BLE001 — a shape change shouldn't break a thread
        logger.warning("Working memory %s is unreadable (%s); treating as absent.", session_id, exc)
        return None


def append_turn(session_id: str, user_id: str, turn: Turn) -> bool:
    """Add a message to the thread. False when there's no session to add to."""
    wm = get_working(session_id, user_id)
    if wm is None:
        return False
    wm.turns.append(turn)
    if len(wm.turns) > MAX_TURNS:
        del wm.turns[:-MAX_TURNS]  # keep the most recent
    return _save(wm)


def set_run_state(
    session_id: str,
    user_id: str,
    *,
    plan: list[dict[str, Any]] | None = None,
    findings: dict[str, Any] | None = None,
    assessment: dict[str, Any] | None = None,
    entities: list[dict[str, Any]] | None = None,
    context_type: str | None = None,
) -> bool:
    """Record what a completed run produced. Only non-None fields are written."""
    wm = get_working(session_id, user_id)
    if wm is None:
        return False
    if plan is not None:
        wm.plan = plan
    if findings is not None:
        wm.findings = findings
    if assessment is not None:
        wm.assessment = assessment
    if entities is not None:
        wm.entities = entities
    if context_type is not None:
        wm.context_type = context_type
    return _save(wm)


def touch(session_id: str, user_id: str) -> bool:
    """Refresh the TTL so an active thread doesn't expire mid-conversation."""
    wm = get_working(session_id, user_id)
    if wm is None:
        return False
    return _save(wm)


def delete_session(session_id: str, user_id: str) -> bool:
    """Drop a thread. Analysts must be able to clear their own context."""
    wm = get_working(session_id, user_id)
    if wm is None:
        return False
    client = _redis()
    if client is None:
        _fallback.pop(session_id, None)
        return True
    try:
        client.delete(_key(session_id))
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("Working memory delete failed for %s: %s", session_id, exc)
        return False


def entities_from_assessment(assessment: dict[str, Any]) -> list[dict[str, Any]]:
    """Entity list for a completed assessment, for anaphora ("that supply chain").

    Read straight off the structured output — `entity_graph.entities` and
    `query.target_entities` are already built by the pipeline, so no LLM pass is
    needed to know what a thread is about.
    """
    out: list[dict[str, Any]] = []
    seen: set[str] = set()

    for ent in (assessment.get("entity_graph") or {}).get("entities") or []:
        if not isinstance(ent, dict):
            continue
        name = (ent.get("name") or "").strip()
        if not name or name.lower() in seen:
            continue
        seen.add(name.lower())
        out.append(
            {
                "entity_id": ent.get("id"),
                "name": name,
                "entity_type": ent.get("entity_type"),
                "country": ent.get("country"),
            }
        )

    for name in (assessment.get("query") or {}).get("target_entities") or []:
        if isinstance(name, str) and name.strip() and name.lower() not in seen:
            seen.add(name.lower())
            out.append({"entity_id": None, "name": name.strip()})

    return out


# ===========================================================================
# Long-term memory (Phase 5): extracted, durable facts with semantic recall.
#
# SQLite (agent_memories) is the SYSTEM OF RECORD; a Redis vector index over
# those rows is a derived accelerator. Losing Redis loses fast semantic recall,
# never the facts — they re-index from SQLite. With no Redis/embeddings, recall
# degrades to a lexical scan of the user's recent memories: worse, never wrong,
# never a 500.
#
# Scoping is per-user (the auth'd username), enforced in every query. Retrieval
# is used PRE-decompose by the orchestrator to sharpen the plan, and exposed as a
# tool; either way memories are injected as UNVERIFIED PRIOR WORK and never enter
# an assessment's cited sources.
# ===========================================================================

# RedisVL index over memories. Distinct from the entity index (per-user, different
# fields), so it lives here rather than in vector_index.py.
_MEM_INDEX_NAME = "emissary_memories"
_MEM_PREFIX = "mem:"
_MEM_META_KEY = "emissary:memindex:meta"
_MEM_SCHEMA_VERSION = 1

# Semantic-dedup threshold: a new memory this close to an existing one (same
# entities) is a restatement — merge + reinforce rather than insert a duplicate.
_SEMANTIC_DEDUP_MIN = 0.93

# Per-(user, entity) soft cap before compaction folds the oldest low-value
# memories into a summary.
MEM_PER_ENTITY_CAP = 40

_mem_index = None
_mem_index_ready = False
_mem_meta_error = ""

_MEMORY_TYPES = ("fact", "preference", "summary")


@dataclass
class Memory:
    """One durable, extracted fact."""

    text: str
    memory_type: str = "fact"
    topics: list[str] = field(default_factory=list)
    entity_names: list[str] = field(default_factory=list)
    entity_ids: list[str] = field(default_factory=list)
    confidence: str = "MEDIUM"
    sources: list[dict] = field(default_factory=list)
    source_analysis_id: str | None = None
    source_session_id: str | None = None
    memory_id: str | None = None
    created_at: str | None = None
    last_seen_at: str | None = None
    seen_count: int = 1

    def normalized_hash(self) -> str:
        norm = " ".join((self.text or "").lower().split())
        return hashlib.sha256(norm.encode()).hexdigest()


def _norm_names(names: list[str]) -> list[str]:
    out, seen = [], set()
    for n in names or []:
        v = (n or "").strip().lower()
        if v and v not in seen:
            seen.add(v)
            out.append(v)
    return out


def _sanitize_memory(m: Memory) -> Memory | None:
    """Clamp a memory to storable shape, or None if it isn't worth storing."""
    text = " ".join((m.text or "").split()).strip()
    if len(text) < 8:  # empty / junk
        return None
    m.text = text[:1000]
    m.memory_type = m.memory_type if m.memory_type in _MEMORY_TYPES else "fact"
    conf = str(m.confidence or "MEDIUM").upper()
    m.confidence = conf if conf in (c.value for c in Confidence) else "MEDIUM"
    m.entity_names = _norm_names(m.entity_names)[:12]
    m.topics = _norm_names(m.topics)[:8]
    m.sources = [s for s in (m.sources or []) if isinstance(s, dict) and s.get("name")][:8]
    return m


# --- SQLite system of record ------------------------------------------------


def _mem_search_text(m: Memory) -> str:
    """The string embedded + indexed: the fact plus the entities it's about."""
    return " ".join([m.text, " ".join(m.entity_names)]).strip()


def remember(user_id: str, memories: list[Memory]) -> int:
    """Persist facts for a user (exact + semantic dedup). Returns count written.

    SQLite first (durable), then best-effort index into Redis. A memory whose
    normalized text already exists for this user is *reinforced* (seen_count++,
    sources/topics unioned) instead of duplicated.
    """
    from src.db import _now, get_db, row_to_agent_memory

    written = 0
    for raw in memories:
        m = _sanitize_memory(raw)
        if m is None:
            continue
        digest = m.normalized_hash()
        now = _now()
        conn = get_db()
        try:
            existing = conn.execute(
                "SELECT * FROM agent_memories WHERE user_id = ? AND text_hash = ?",
                (user_id, digest),
            ).fetchone()
            if existing:
                # Exact restatement -> reinforce, don't duplicate.
                prior = row_to_agent_memory(existing)
                merged_sources = _union_sources(prior["sources"], m.sources)
                merged_topics = _norm_names(prior["topics"] + m.topics)
                conf = _stronger_conf(prior["confidence"], m.confidence)
                conn.execute(
                    "UPDATE agent_memories SET seen_count = seen_count + 1, last_seen_at = ?, "
                    "sources = ?, topics = ?, confidence = ? WHERE memory_id = ?",
                    (
                        now,
                        json.dumps(merged_sources),
                        json.dumps(merged_topics),
                        conf,
                        prior["memory_id"],
                    ),
                )
                conn.commit()
                mem_id = prior["memory_id"]
            else:
                mem_id = uuid.uuid4().hex
                conn.execute(
                    "INSERT INTO agent_memories (memory_id, user_id, text, memory_type, topics, "
                    "entity_names, entity_ids, confidence, sources, source_analysis_id, "
                    "source_session_id, text_hash, created_at, last_seen_at, seen_count) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,1)",
                    (
                        mem_id,
                        user_id,
                        m.text,
                        m.memory_type,
                        json.dumps(m.topics),
                        json.dumps(m.entity_names),
                        json.dumps(m.entity_ids),
                        m.confidence,
                        json.dumps(m.sources),
                        m.source_analysis_id,
                        m.source_session_id,
                        digest,
                        now,
                        now,
                    ),
                )
                conn.commit()
                written += 1
        finally:
            conn.close()

        m.memory_id = mem_id
        _index_memory(user_id, m)

    return written


def list_memories(user_id: str, limit: int = 200) -> list[dict]:
    """A user's memories, most recent first. Their own only."""
    from src.db import get_db, row_to_agent_memory

    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT * FROM agent_memories WHERE user_id = ? ORDER BY last_seen_at DESC LIMIT ?",
            (user_id, limit),
        ).fetchall()
    finally:
        conn.close()
    return [row_to_agent_memory(r) for r in rows]


def forget(memory_id: str, user_id: str) -> bool:
    """Delete a memory the user owns. Analysts must be able to see + kill these."""
    from src.db import get_db

    conn = get_db()
    try:
        cur = conn.execute(
            "DELETE FROM agent_memories WHERE memory_id = ? AND user_id = ?",
            (memory_id, user_id),
        )
        deleted = cur.rowcount
        conn.commit()
    finally:
        conn.close()
    if deleted:
        _deindex_memory(user_id, memory_id)
    return bool(deleted)


def _union_sources(a: list[dict], b: list[dict]) -> list[dict]:
    out: dict[str, dict] = {}
    for s in (a or []) + (b or []):
        if isinstance(s, dict) and s.get("name"):
            out.setdefault(s["name"].lower(), s)
    return list(out.values())[:8]


def _stronger_conf(a: str, b: str) -> str:
    order = {"LOW": 0, "MEDIUM": 1, "HIGH": 2}
    return a if order.get(a, 1) >= order.get(b, 1) else b


# --- Redis vector index (derived) -------------------------------------------


def _mem_schema(dims: int) -> dict:
    return {
        "index": {"name": _MEM_INDEX_NAME, "prefix": _MEM_PREFIX, "storage_type": "json"},
        "fields": [
            {"name": "memory_id", "type": "tag"},
            {"name": "user_id", "type": "tag"},  # MANDATORY filter on every recall
            {"name": "text", "type": "text"},
            {"name": "memory_type", "type": "tag"},
            {"name": "topics", "type": "tag", "attrs": {"separator": "|"}},
            {"name": "entity_names", "type": "tag", "attrs": {"separator": "|"}},
            {"name": "confidence", "type": "tag"},
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


def _mem_available() -> bool:
    if _mem_meta_error:
        return False
    try:
        from src.common.embeddings import embeddings_enabled
        from src.common.redis_client import capabilities

        return capabilities().search and embeddings_enabled()
    except Exception:  # noqa: BLE001
        return False


def _mem_check_meta(client) -> bool:
    global _mem_meta_error
    from src.common.config import config

    want = {
        "model": config.embedding_model,
        "dims": config.embedding_dims,
        "schema_v": _MEM_SCHEMA_VERSION,
    }
    try:
        raw = client.get(_MEM_META_KEY)
    except Exception:  # noqa: BLE001
        return True
    if not raw:
        try:
            client.set(_MEM_META_KEY, json.dumps(want))
        except Exception:  # noqa: BLE001
            pass
        return True
    try:
        have = json.loads(raw)
    except Exception:  # noqa: BLE001
        return True
    if have != want:
        _mem_meta_error = (
            f"memory index built for {have} but config is {want} — refusing to query it. "
            f"Run `python -m scripts.reindex_memories --force`."
        )
        logger.error("Agent-memory vector index DISABLED: %s", _mem_meta_error)
        return False
    return True


def _get_mem_index():
    global _mem_index, _mem_index_ready
    if _mem_index_ready:
        return _mem_index
    if not _mem_available():
        return None
    try:
        from redisvl.index import SearchIndex

        from src.common.config import config
        from src.common.redis_client import get_redis

        client = get_redis()
        if client is None or not _mem_check_meta(client):
            return None
        index = SearchIndex.from_dict(_mem_schema(config.embedding_dims), redis_client=client)
        index.create(overwrite=False)
        _mem_index = index
        _mem_index_ready = True
        return _mem_index
    except Exception as exc:  # noqa: BLE001
        logger.warning("Agent-memory index unavailable (recall stays lexical): %s", exc)
        return None


def _mem_key(user_id: str, memory_id: str) -> str:
    return f"{_MEM_PREFIX}{user_id}:{memory_id}"


def _index_memory(user_id: str, m: Memory) -> None:
    """Best-effort embed + upsert into the Redis memory index. Never raises."""
    index = _get_mem_index()
    if index is None or not m.memory_id:
        return
    try:
        import asyncio

        async def _do():
            from src.common.embeddings import embed

            vec = await embed(_mem_search_text(m))
            if vec is None:
                return
            index.load(
                [
                    {
                        "memory_id": m.memory_id,
                        "user_id": user_id,
                        "text": m.text,
                        "memory_type": m.memory_type,
                        "topics": "|".join(m.topics),
                        "entity_names": "|".join(m.entity_names),
                        "confidence": m.confidence,
                        "embedding": vec,
                    }
                ],
                keys=[_mem_key(user_id, m.memory_id)],
            )

        try:
            loop = asyncio.get_running_loop()
            loop.create_task(_do())
        except RuntimeError:
            asyncio.run(_do())
    except Exception as exc:  # noqa: BLE001
        logger.debug("Memory index write skipped for %s: %s", m.memory_id, exc)


def _deindex_memory(user_id: str, memory_id: str) -> None:
    try:
        from src.common.redis_client import get_redis

        client = get_redis()
        if client is not None:
            client.delete(_mem_key(user_id, memory_id))
    except Exception:  # noqa: BLE001
        pass


async def recall(
    query: str,
    *,
    user_id: str,
    k: int = 8,
    entity_names: list[str] | None = None,
    memory_types: list[str] | None = None,
) -> list[tuple[dict, float]]:
    """Return a user's memories most relevant to ``query`` as ``[(memory, score)]``.

    Semantic (vector KNN + user/entity/type tag filters) when the index is
    available; otherwise a lexical scan of the user's recent memories. Always
    scoped to ``user_id`` — never another analyst's.
    """
    index = _get_mem_index()
    if index is None:
        return _lexical_recall(query, user_id, k, entity_names, memory_types)

    try:
        from redisvl.query import VectorQuery
        from redisvl.query.filter import Tag

        from src.common.embeddings import embed

        vec = await embed(query)
        if vec is None:
            return _lexical_recall(query, user_id, k, entity_names, memory_types)

        flt = Tag("user_id") == user_id
        if memory_types:
            flt = flt & (Tag("memory_type") == list(memory_types))
        if entity_names:
            flt = flt & (Tag("entity_names") == _norm_names(entity_names))

        q = VectorQuery(
            vector=vec,
            vector_field_name="embedding",
            filter_expression=flt,
            num_results=k,
            return_fields=["memory_id"],
        )
        rows = index.query(q)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Memory vector recall failed (using lexical): %s", exc)
        return _lexical_recall(query, user_id, k, entity_names, memory_types)

    scores = {}
    for r in rows:
        mid = r.get("memory_id")
        if not mid:
            continue
        try:
            dist = float(r.get("vector_distance", 1.0))
        except (TypeError, ValueError):
            dist = 1.0
        scores[mid] = max(0.0, min(1.0, 1.0 - dist))

    if not scores:
        return []
    hydrated = {m["memory_id"]: m for m in _memories_by_ids(user_id, list(scores))}
    out = [(hydrated[mid], s) for mid, s in scores.items() if mid in hydrated]
    out.sort(key=lambda x: x[1], reverse=True)
    return out


def _memories_by_ids(user_id: str, memory_ids: list[str]) -> list[dict]:
    from src.db import get_db, row_to_agent_memory

    if not memory_ids:
        return []
    conn = get_db()
    try:
        ph = ",".join("?" for _ in memory_ids)
        rows = conn.execute(
            f"SELECT * FROM agent_memories WHERE user_id = ? AND memory_id IN ({ph})",
            [user_id, *memory_ids],
        ).fetchall()
    finally:
        conn.close()
    return [row_to_agent_memory(r) for r in rows]


def _lexical_recall(
    query: str,
    user_id: str,
    k: int,
    entity_names: list[str] | None,
    memory_types: list[str] | None,
) -> list[tuple[dict, float]]:
    """Fallback recall: Jaccard token overlap over the user's recent memories.

    Worse than semantic, but correct and dependency-free — this is what runs with
    no Redis 8 / no Voyage key (i.e. today's infra), so the feature still works.
    """
    from src.common.similarity import jaccard_similarity

    wanted_names = set(_norm_names(entity_names or []))
    wanted_types = set(memory_types or [])
    scored: list[tuple[dict, float]] = []
    for m in list_memories(user_id, limit=500):
        if wanted_types and m["memory_type"] not in wanted_types:
            continue
        name_bonus = 0.3 if wanted_names & set(m["entity_names"]) else 0.0
        text_score = jaccard_similarity(query, f"{m['text']} {' '.join(m['entity_names'])}")
        score = min(1.0, text_score + name_bonus)
        if score > 0:
            scored.append((m, score))
    scored.sort(key=lambda x: x[1], reverse=True)
    return scored[:k]


async def maybe_merge_semantic_duplicate(user_id: str, m: Memory) -> bool:
    """If a near-identical memory already exists (same entities), reinforce it.

    Returns True if merged (caller should NOT insert). Only meaningful when the
    vector index is available; otherwise returns False and exact-hash dedup in
    remember() is the only guard.
    """
    if not _mem_available():
        return False
    hits = await recall(_mem_search_text(m), user_id=user_id, k=1, entity_names=m.entity_names)
    if not hits:
        return False
    top, score = hits[0]
    if score < _SEMANTIC_DEDUP_MIN:
        return False
    from src.db import _now, get_db

    conn = get_db()
    try:
        conn.execute(
            "UPDATE agent_memories SET seen_count = seen_count + 1, last_seen_at = ?, "
            "sources = ?, confidence = ? WHERE memory_id = ?",
            (
                _now(),
                json.dumps(_union_sources(top["sources"], m.sources)),
                _stronger_conf(top["confidence"], m.confidence),
                top["memory_id"],
            ),
        )
        conn.commit()
    finally:
        conn.close()
    return True


def memory_backend_name() -> str:
    """ "redis" (vector recall) or "lexical" (fallback) — for /api/health."""
    return "redis" if _mem_available() else "lexical"


def reset_for_tests() -> None:
    global _mem_index, _mem_index_ready, _mem_meta_error
    _fallback.clear()
    _mem_index = None
    _mem_index_ready = False
    _mem_meta_error = ""
