"""Follow-up context resolution: hydrate from a thread WITHOUT breaking the old contract.

`POST /api/followup` currently only works because the browser posts the entire
prior assessment back on every question. Phase 3 lets the server hydrate that from
working memory instead — but the client-supplied path must keep behaving *exactly*
as it does today, or the frontend has to change in lockstep and the migration
becomes a flag day.

These tests assert on the resolved context and the generated system prompt rather
than on Claude's answer: the prompt is deterministic, the answer isn't.
"""

from __future__ import annotations

import pytest

from src.common import agent_memory
from src.routers.followup import (
    FollowUpMessage,
    FollowUpRequest,
    _build_orchestrator_followup_system,
    _resolve_context,
)


@pytest.fixture(autouse=True)
def _reset():
    agent_memory.reset_for_tests()
    yield
    agent_memory.reset_for_tests()


def _assessment(summary: str = "Fujian Jinhua is on the BIS Entity List."):
    return {
        "executive_summary": summary,
        "query": {"raw_query": "sanction Fujian Jinhua", "target_entities": ["Fujian Jinhua"]},
        "scenario_type": "sanction_impact",
        "findings": [{"category": "sanctions", "finding": "Entity List", "confidence": "HIGH"}],
        "friendly_fire": [],
        "recommendations": ["Watch UMC"],
        "confidence_summary": {"overall": "HIGH"},
        "entity_graph": {"entities": [], "relationships": []},
        "tool_results": {"step_1": {"data": "x"}},
    }


# --- The old contract must not move ------------------------------------------


def test_old_contract_prompt_is_byte_identical():
    """context + no session_id -> exactly today's prompt.

    This is what lets the server start owning the thread while the current
    frontend keeps posting context, unaware. If this ever fails, the frontend and
    backend must ship together.
    """
    ctx = _assessment()

    # What the app does today (and what the current frontend sends).
    expected = _build_orchestrator_followup_system(ctx)

    # What it does after the change, for the same request.
    req = FollowUpRequest(question="Why?", context_type="orchestrator", context=ctx)
    resolved_ctx, resolved_type, _ = _resolve_context(req, "analyst")
    actual = _build_orchestrator_followup_system(resolved_ctx)

    assert actual == expected
    # Passed through unchanged (pydantic copies the dict on construction, so this
    # is value equality, not identity).
    assert resolved_ctx == ctx
    assert resolved_type == "orchestrator"


def test_client_context_wins_over_session():
    """A client that still posts context gets its context, not the stored one."""
    sid = agent_memory.new_session("analyst")
    agent_memory.set_run_state(sid, "analyst", assessment=_assessment("STORED"))

    req = FollowUpRequest(
        question="Why?",
        context_type="orchestrator",
        context=_assessment("CLIENT"),
        session_id=sid,
    )
    ctx, _, _ = _resolve_context(req, "analyst")
    assert ctx["executive_summary"] == "CLIENT"


def test_client_history_wins_over_session_turns():
    sid = agent_memory.new_session("analyst")
    agent_memory.set_run_state(sid, "analyst", assessment=_assessment())
    agent_memory.append_turn(sid, "analyst", agent_memory.Turn(role="user", text="STORED TURN"))

    req = FollowUpRequest(
        question="Why?",
        session_id=sid,
        history=[FollowUpMessage(role="user", text="CLIENT TURN")],
    )
    _, _, history = _resolve_context(req, "analyst")
    assert [h.text for h in history] == ["CLIENT TURN"]


# --- The new path ------------------------------------------------------------


def test_hydrates_from_session_when_no_context():
    """No context + session_id -> the stored assessment, and the SAME prompt.

    wm.assessment is the identical shape the browser posts back, which is why the
    four _build_*_followup_system builders needed zero changes.
    """
    sid = agent_memory.new_session("analyst")
    stored = _assessment("Fujian Jinhua's DRAM line came from UMC.")
    agent_memory.set_run_state(sid, "analyst", assessment=stored)

    req = FollowUpRequest(question="What else is exposed?", session_id=sid)
    ctx, ctx_type, _ = _resolve_context(req, "analyst")

    assert ctx["executive_summary"] == "Fujian Jinhua's DRAM line came from UMC."
    assert ctx_type == "orchestrator"

    # The hydrated prompt is what a client-supplied one would have produced.
    assert _build_orchestrator_followup_system(ctx) == _build_orchestrator_followup_system(stored)


def test_hydrated_prompt_carries_the_prior_summary():
    sid = agent_memory.new_session("analyst")
    agent_memory.set_run_state(
        sid, "analyst", assessment=_assessment("UMC transferred the process.")
    )

    req = FollowUpRequest(question="Who else?", session_id=sid)
    ctx, _, _ = _resolve_context(req, "analyst")
    prompt = _build_orchestrator_followup_system(ctx)

    assert "UMC transferred the process." in prompt
    assert "sanction Fujian Jinhua" in prompt  # the original question travels too


def test_session_turns_become_history():
    sid = agent_memory.new_session("analyst")
    agent_memory.set_run_state(sid, "analyst", assessment=_assessment())
    agent_memory.append_turn(sid, "analyst", agent_memory.Turn(role="user", text="Q1"))
    agent_memory.append_turn(sid, "analyst", agent_memory.Turn(role="assistant", text="A1"))

    req = FollowUpRequest(question="Q2", session_id=sid)
    _, _, history = _resolve_context(req, "analyst")

    assert [(h.role, h.text) for h in history] == [("user", "Q1"), ("assistant", "A1")]


def test_context_type_follows_the_session():
    sid = agent_memory.new_session("analyst")
    agent_memory.set_run_state(sid, "analyst", assessment=_assessment(), context_type="vessel")

    req = FollowUpRequest(question="Where is it?", session_id=sid)  # default 'orchestrator'
    _, ctx_type, _ = _resolve_context(req, "analyst")
    assert ctx_type == "vessel"  # the stored thread knows what it is about


# --- Isolation + empty states ------------------------------------------------


def test_cannot_hydrate_from_another_users_session():
    sid = agent_memory.new_session("alice")
    agent_memory.set_run_state(sid, "alice", assessment=_assessment("alice only"))

    req = FollowUpRequest(question="What did alice ask?", session_id=sid)
    ctx, _, _ = _resolve_context(req, "bob")
    assert ctx == {}  # -> the route 400s rather than leaking


def test_no_context_and_no_session_resolves_empty():
    req = FollowUpRequest(question="Why?")
    ctx, _, _ = _resolve_context(req, "analyst")
    assert ctx == {}  # -> 400, same as an empty context today


def test_session_without_a_completed_analysis_resolves_empty():
    sid = agent_memory.new_session("analyst")  # no run yet
    req = FollowUpRequest(question="Why?", session_id=sid)
    ctx, _, _ = _resolve_context(req, "analyst")
    assert ctx == {}
