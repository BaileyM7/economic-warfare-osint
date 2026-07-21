"""Session threading through the real /api/analyze endpoints.

Drives the app over HTTP rather than calling functions, so the additive-contract
claim ("old clients are unaffected") is tested the way a client experiences it.

The orchestrator itself is stubbed: this is about session plumbing, not about
Claude. Whether the pipeline produces a good assessment is a different test's job.
"""

from __future__ import annotations

import pytest

from src.common import agent_memory


@pytest.fixture(autouse=True)
def _reset():
    agent_memory.reset_for_tests()
    yield
    agent_memory.reset_for_tests()


def _assessment(summary="Fujian Jinhua is on the BIS Entity List."):
    return {
        "executive_summary": summary,
        "query": {"raw_query": "sanction Fujian Jinhua", "target_entities": ["Fujian Jinhua"]},
        "scenario_type": "sanction_impact",
        "findings": [],
        "friendly_fire": [],
        "recommendations": [],
        "confidence_summary": {},
        "entity_graph": {
            "entities": [{"id": "umc", "name": "UMC", "entity_type": "company", "country": "TW"}],
            "relationships": [],
        },
        "tool_results": {},
    }


def test_analyze_returns_a_session_id(app_client, auth_headers):
    r = app_client.post(
        "/api/analyze", json={"query": "sanction Fujian Jinhua"}, headers=auth_headers
    )
    assert r.status_code == 200, r.text

    body = r.json()
    assert body["session_id"], "additive field must be populated for new clients"
    # Old clients read these two and ignore the rest — unchanged.
    assert body["analysis_id"] and body["status"] == "running"


def test_analyze_reuses_a_supplied_session(app_client, auth_headers):
    first = app_client.post("/api/analyze", json={"query": "q1"}, headers=auth_headers).json()
    sid = first["session_id"]

    second = app_client.post(
        "/api/analyze", json={"query": "q2", "session_id": sid}, headers=auth_headers
    ).json()

    assert second["session_id"] == sid, "the thread should continue, not restart"


def test_stale_session_id_starts_a_new_thread_not_an_error(app_client, auth_headers):
    """A stale tab must not 4xx — it just gets a fresh thread."""
    r = app_client.post(
        "/api/analyze", json={"query": "q", "session_id": "expired-or-bogus"}, headers=auth_headers
    )
    assert r.status_code == 200
    assert r.json()["session_id"] != "expired-or-bogus"


def test_another_users_session_id_is_not_hijackable(app_client, auth_headers):
    """Passing someone else's session must not attach you to their thread."""
    victim = agent_memory.new_session("someone-else")

    r = app_client.post(
        "/api/analyze", json={"query": "q", "session_id": victim}, headers=auth_headers
    )
    assert r.status_code == 200
    assert r.json()["session_id"] != victim  # silently re-homed to a fresh thread


def test_completed_run_is_recorded_in_the_thread(app_client, auth_headers, monkeypatch):
    """The live path writes turns + assessment + entities into working memory."""
    import src.routers.orchestrator as orch

    sid = agent_memory.new_session("tester")
    orch._remember_run(sid, "tester", "sanction Fujian Jinhua", "abc123", _assessment())

    wm = agent_memory.get_working(sid, "tester")
    assert [t.role for t in wm.turns] == ["user", "assistant"]
    assert wm.turns[0].text == "sanction Fujian Jinhua"
    assert wm.turns[1].text.startswith("Fujian Jinhua is on the BIS")
    assert wm.turns[0].analysis_id == "abc123"
    assert wm.assessment is not None
    # Entities come off the structured output — no extra LLM call.
    assert {e["name"] for e in wm.entities} == {"UMC", "Fujian Jinhua"}


def test_replayed_run_is_remembered_too(monkeypatch):
    """A warm cache replay must still thread.

    This is the easy bug: the prewarm path returns early, so if it skips memory
    the demo questions — the ones most likely to be shown — produce a session that
    has no idea what was just asked.
    """
    import asyncio

    import src.routers.orchestrator as orch
    from src.common.analyses import get_analysis_store

    _analyses = get_analysis_store()

    sid = agent_memory.new_session("tester")
    cached = {"events": [], "result": _assessment("cached replay summary")}

    monkeypatch.setattr(orch, "_PREWARM", True)
    monkeypatch.setattr(orch, "get_cached", lambda ns, **kw: cached)

    _analyses["rep1"] = {
        "analysis_id": "rep1",
        "status": "running",
        "progress": [],
        "events": [],
        "result": None,
    }
    asyncio.run(orch._run_analysis("rep1", "sanction Fujian Jinhua", sid, "tester"))

    wm = agent_memory.get_working(sid, "tester")
    assert wm.assessment is not None, "the replay path must write working memory"
    assert wm.assessment["executive_summary"] == "cached replay summary"
    assert [t.role for t in wm.turns] == ["user", "assistant"]


def test_memory_failure_never_breaks_an_analysis(monkeypatch):
    """Memory is an enhancement; the assessment is the product."""
    import src.routers.orchestrator as orch

    def _boom(*a, **kw):
        raise RuntimeError("redis on fire")

    monkeypatch.setattr(agent_memory, "append_turn", _boom)

    # Must not raise.
    orch._remember_run("sid", "tester", "q", "a1", _assessment())


def test_health_reports_the_memory_backend(app_client):
    r = app_client.get("/api/health")
    assert r.json()["working_memory"]["backend"] == "memory"  # no REDIS_URL in tests
