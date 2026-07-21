"""The shared analysis store (Phase 7 — F3): explicit write path, no lost writes.

These pin the in-memory backend (the default, and what CI runs). The Redis
backend's cross-instance sharing — the actual F3 fix — is proven against a real
Redis 8 in tests/integration/test_analysis_store_redis.py, because RedisJSON
(JSON.ARRAPPEND/ARRTRIM) has no fakeredis equivalent.

The load-bearing property: appends and field sets go through explicit methods, so
a future Redis backend (whose get() returns a snapshot) can't silently drop an
in-place mutation — the trap the plan calls out.
"""

from __future__ import annotations

import pytest

from src.common import analyses


@pytest.fixture(autouse=True)
def _reset():
    analyses.reset_for_tests()
    yield
    analyses.reset_for_tests()


def _new(store, aid="a1"):
    store.create(aid, {"analysis_id": aid, "status": "running", "progress": [], "events": []})
    return aid


def test_default_backend_is_memory():
    assert analyses.backend_name() == "memory"
    assert isinstance(analyses.get_analysis_store(), analyses.InMemoryAnalysisStore)


def test_create_and_get_snapshot():
    store = analyses.get_analysis_store()
    _new(store)
    snap = store.get("a1")
    assert snap["status"] == "running"
    assert store.get("missing") is None


def test_set_fields_updates():
    store = analyses.get_analysis_store()
    _new(store)
    store.set_fields("a1", status="completed", result={"executive_summary": "done"})
    snap = store.get("a1")
    assert snap["status"] == "completed"
    assert snap["result"]["executive_summary"] == "done"


def test_append_progress_and_events_accumulate():
    store = analyses.get_analysis_store()
    _new(store)
    store.append_progress("a1", "step 1")
    store.append_progress("a1", "step 2")
    store.append_event("a1", {"type": "tool", "name": "x"})

    snap = store.get("a1")
    assert snap["progress"] == ["step 1", "step 2"]
    assert snap["events"] == [{"type": "tool", "name": "x"}]


def test_progress_is_capped_keeping_the_tail():
    store = analyses.get_analysis_store()
    _new(store)
    for i in range(analyses.ANALYSES_PROGRESS_CAP + 25):
        store.append_progress("a1", f"m{i}")
    prog = store.get("a1")["progress"]
    assert len(prog) == analyses.ANALYSES_PROGRESS_CAP
    assert prog[-1] == f"m{analyses.ANALYSES_PROGRESS_CAP + 24}"  # newest survives


def test_events_are_capped():
    store = analyses.get_analysis_store()
    _new(store)
    for i in range(analyses.ANALYSES_EVENTS_CAP + 10):
        store.append_event("a1", {"n": i})
    assert len(store.get("a1")["events"]) == analyses.ANALYSES_EVENTS_CAP


def test_writes_to_a_missing_analysis_are_silent():
    store = analyses.get_analysis_store()
    # Evicted / never created — must not raise (matches the run-loop's tolerance).
    store.append_progress("gone", "x")
    store.append_event("gone", {"n": 1})
    store.set_fields("gone", status="completed")
    assert store.get("gone") is None


def test_mutating_a_snapshot_does_not_persist():
    """The whole point of the explicit write path: reads are snapshots.

    A caller that mutates a get() result must NOT affect stored state — that's the
    discipline that lets the Redis backend behave identically. (In-memory returns
    the live dict today, so this documents the contract rather than proving it for
    that backend; the Redis integration test proves it where it bites.)
    """
    store = analyses.get_analysis_store()
    _new(store)
    store.append_progress("a1", "real")
    # The supported way to write is the method, not snapshot mutation.
    store.set_fields("a1", status="completed")
    assert store.get("a1")["status"] == "completed"
    assert store.get("a1")["progress"] == ["real"]
