"""Shared analysis status/result store (Phase 7 — closes fragility F3).

Holds orchestrator analysis state keyed by analysis-id: status, progress log,
structured swarm events, the final result, session/replay metadata. Both the
orchestrator router (writer) and the briefings router (reader) go through the same
store object.

**Why this is an interface, not a bare dict.** The old store was a process-local
`TTLCache`, so it (a) vanished on restart — a mid-run analysis 404'd — and (b)
made running >1 web instance impossible (F3). Moving it to Redis fixes both. But
the run loop mutated entries **in place** (`entry["progress"].append(...)`), and a
Redis-backed value is a *deserialized snapshot* — those mutations would be
silently discarded. So the write path is explicit methods
(`append_progress` / `append_event` / `set_fields`), implemented server-side on
Redis with `JSON.ARRAPPEND` + `JSON.ARRTRIM` so the caps are atomic and two
writers can't clobber each other. Reads return a snapshot; callers must not mutate
it.

Backends (``ANALYSES_BACKEND``, default ``memory``):
  * ``memory`` — the original `TTLCache(50, 1h)`. Process-local; F3 remains but
    behaviour is byte-identical to before. The default, so nothing changes until
    you opt in.
  * ``redis``  — RedisJSON docs at ``emissary:analysis:{id}`` with a 1h TTL.
    Survives restarts and is shared across instances. Needs Redis 8 (JSON.*);
    falls back to memory (loudly) if unavailable.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any, Protocol

from cachetools import TTLCache

logger = logging.getLogger(__name__)

ANALYSES_MAX = 50
ANALYSES_TTL_SEC = 3600
ANALYSES_PROGRESS_CAP = 200
# Structured agent-swarm events (one running + one done per tool call). Bounded
# generously — a heavy analysis fires a few dozen tool calls.
ANALYSES_EVENTS_CAP = 400

_KEY_PREFIX = "emissary:analysis:"
# Result payloads carry full GDELT/Comtrade JSON. Refuse to store one larger than
# this rather than let a single analysis eat the instance.
_MAX_RESULT_BYTES = 5 * 1024 * 1024


class AnalysisStore(Protocol):
    """The write path is explicit methods; reads return a read-only snapshot."""

    def create(self, analysis_id: str, initial: dict[str, Any]) -> None: ...
    def get(self, analysis_id: str) -> dict[str, Any] | None: ...
    def set_fields(self, analysis_id: str, **fields: Any) -> None: ...
    def append_progress(self, analysis_id: str, msg: str) -> None: ...
    def append_event(self, analysis_id: str, event: dict[str, Any]) -> None: ...


# --- In-memory (default) -----------------------------------------------------


class InMemoryAnalysisStore:
    """Wraps the original TTLCache. Byte-identical behaviour to pre-Phase-7.

    Supports dict-style access (``store[id] = ...`` / ``id in store`` / ``.get``)
    as a convenience for readers and tests; the orchestrator run loop still uses
    the explicit write methods so the two backends behave the same.
    """

    def __init__(self) -> None:
        self._cache: TTLCache[str, dict[str, Any]] = TTLCache(
            maxsize=ANALYSES_MAX, ttl=ANALYSES_TTL_SEC
        )

    def create(self, analysis_id: str, initial: dict[str, Any]) -> None:
        self._cache[analysis_id] = dict(initial)

    def get(self, analysis_id: str) -> dict[str, Any] | None:
        return self._cache.get(analysis_id)

    def set_fields(self, analysis_id: str, **fields: Any) -> None:
        entry = self._cache.get(analysis_id)
        if entry is not None:
            entry.update(fields)

    def append_progress(self, analysis_id: str, msg: str) -> None:
        entry = self._cache.get(analysis_id)
        if entry is None:
            return
        progress = entry.setdefault("progress", [])
        progress.append(msg)
        if len(progress) > ANALYSES_PROGRESS_CAP:
            del progress[:-ANALYSES_PROGRESS_CAP]

    def append_event(self, analysis_id: str, event: dict[str, Any]) -> None:
        entry = self._cache.get(analysis_id)
        if entry is None:
            return
        events = entry.setdefault("events", [])
        events.append(event)
        if len(events) > ANALYSES_EVENTS_CAP:
            del events[:-ANALYSES_EVENTS_CAP]

    # Dict conveniences (readers + tests).
    def __setitem__(self, analysis_id: str, value: dict[str, Any]) -> None:
        self.create(analysis_id, value)

    def __getitem__(self, analysis_id: str) -> dict[str, Any]:
        return self._cache[analysis_id]

    def __contains__(self, analysis_id: str) -> bool:
        return analysis_id in self._cache


# --- Redis (opt-in) ----------------------------------------------------------


class RedisAnalysisStore:
    """RedisJSON-backed store. Survives restarts, shared across instances.

    Every write refreshes the 1h TTL. Appends use JSON.ARRAPPEND + JSON.ARRTRIM so
    the cap is enforced atomically server-side — no read-modify-write race between
    concurrent writers. Never raises into the caller; a Redis hiccup logs and the
    update is dropped (the analysis still completes, its live progress just stops
    updating), which is strictly better than 500-ing a running analysis.
    """

    def __init__(self, client) -> None:
        self._r = client

    def _key(self, analysis_id: str) -> str:
        return f"{_KEY_PREFIX}{analysis_id}"

    def create(self, analysis_id: str, initial: dict[str, Any]) -> None:
        try:
            key = self._key(analysis_id)
            self._r.execute_command("JSON.SET", key, "$", json.dumps(initial))
            self._r.expire(key, ANALYSES_TTL_SEC)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Analysis store create failed for %s: %s", analysis_id, exc)

    def get(self, analysis_id: str) -> dict[str, Any] | None:
        try:
            raw = self._r.execute_command("JSON.GET", self._key(analysis_id), "$")
        except Exception as exc:  # noqa: BLE001
            logger.warning("Analysis store read failed for %s: %s", analysis_id, exc)
            return None
        if not raw:
            return None
        # JSON.GET with a `$` path returns a list of matches (here, one doc).
        parsed = json.loads(raw) if isinstance(raw, (str, bytes)) else raw
        if isinstance(parsed, list):
            return parsed[0] if parsed else None
        return parsed

    def set_fields(self, analysis_id: str, **fields: Any) -> None:
        key = self._key(analysis_id)
        try:
            for name, value in fields.items():
                if name == "result" and value is not None:
                    blob = json.dumps(value)
                    if len(blob.encode()) > _MAX_RESULT_BYTES:
                        logger.warning(
                            "Analysis %s result is %d bytes (> cap); storing a stub instead.",
                            analysis_id,
                            len(blob.encode()),
                        )
                        value = {"error": "result too large to store"}
                self._r.execute_command("JSON.SET", key, f"$.{name}", json.dumps(value))
            self._r.expire(key, ANALYSES_TTL_SEC)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Analysis store set_fields failed for %s: %s", analysis_id, exc)

    def append_progress(self, analysis_id: str, msg: str) -> None:
        self._append(analysis_id, "progress", msg, ANALYSES_PROGRESS_CAP)

    def append_event(self, analysis_id: str, event: dict[str, Any]) -> None:
        self._append(analysis_id, "events", event, ANALYSES_EVENTS_CAP)

    def _append(self, analysis_id: str, field: str, value: Any, cap: int) -> None:
        key = self._key(analysis_id)
        try:
            pipe = self._r.pipeline()
            pipe.execute_command("JSON.ARRAPPEND", key, f"$.{field}", json.dumps(value))
            pipe.execute_command("JSON.ARRTRIM", key, f"$.{field}", -cap, -1)
            pipe.expire(key, ANALYSES_TTL_SEC)
            pipe.execute()
        except Exception as exc:  # noqa: BLE001
            logger.warning("Analysis store append(%s) failed for %s: %s", field, analysis_id, exc)

    def __setitem__(self, analysis_id: str, value: dict[str, Any]) -> None:
        self.create(analysis_id, value)

    def __getitem__(self, analysis_id: str) -> dict[str, Any]:
        v = self.get(analysis_id)
        if v is None:
            raise KeyError(analysis_id)
        return v

    def __contains__(self, analysis_id: str) -> bool:
        try:
            return bool(self._r.exists(self._key(analysis_id)))
        except Exception:  # noqa: BLE001
            return False


# --- Selection ---------------------------------------------------------------


def _build_store() -> AnalysisStore:
    backend = os.getenv("ANALYSES_BACKEND", "memory").strip().lower()
    if backend == "redis":
        try:
            from src.common.redis_client import capabilities, get_redis

            client = get_redis()
            if client is not None and capabilities().json:
                logger.info("Analysis store: redis (JSON-backed, shared across instances)")
                return RedisAnalysisStore(client)
            logger.warning(
                "ANALYSES_BACKEND=redis but Redis JSON unavailable (server=%s) — "
                "falling back to in-memory (F3: single-instance only).",
                capabilities().server or "none",
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("ANALYSES_BACKEND=redis init failed (%s); using in-memory.", exc)
    return InMemoryAnalysisStore()


_store: AnalysisStore | None = None


def get_analysis_store() -> AnalysisStore:
    """The process-wide analysis store (built once)."""
    global _store
    if _store is None:
        _store = _build_store()
    return _store


def backend_name() -> str:
    """ "redis" (shared/durable) or "memory" (process-local) — for /api/health."""
    return "redis" if isinstance(get_analysis_store(), RedisAnalysisStore) else "memory"


def reset_for_tests() -> None:
    global _store
    _store = None
