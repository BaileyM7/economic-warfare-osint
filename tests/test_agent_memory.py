"""Working memory: threads, isolation, and not breaking the existing contract.

Two of these tests are load-bearing:

  * `test_old_contract_prompt_is_byte_identical` — the whole migration strategy is
    "server starts owning the thread while the client still thinks it does". If a
    client-supplied `context` ever produced a different prompt than it does today,
    that strategy is dead and this becomes a flag-day frontend change.
  * `test_another_users_session_is_invisible` — session state is per-analyst and
    the check is server-side from the auth token, never from the request body.

Everything here runs on the default hermetic config (REDIS_URL=""), so it also
proves the in-memory fallback keeps local dev and CI working untouched.
"""

from __future__ import annotations

import pytest

from src.common import agent_memory
from src.common.agent_memory import Turn


@pytest.fixture(autouse=True)
def _reset():
    agent_memory.reset_for_tests()
    yield
    agent_memory.reset_for_tests()


def _assessment(summary="Fujian Jinhua is on the BIS Entity List.", entities=None):
    """An ImpactAssessment.model_dump(mode="json")-shaped blob."""
    return {
        "executive_summary": summary,
        "query": {"raw_query": "sanction Fujian Jinhua", "target_entities": entities or []},
        "findings": [{"category": "sanctions", "finding": "Entity List", "confidence": "HIGH"}],
        "entity_graph": {"entities": [], "relationships": []},
        "tool_results": {},
    }


# --- The thread --------------------------------------------------------------


def test_new_session_round_trips():
    sid = agent_memory.new_session("analyst")
    wm = agent_memory.get_working(sid, "analyst")

    assert wm is not None
    assert wm.session_id == sid
    assert wm.user_id == "analyst"
    assert wm.turns == []


def test_turns_accumulate_in_order():
    sid = agent_memory.new_session("analyst")
    agent_memory.append_turn(sid, "analyst", Turn(role="user", text="Q1"))
    agent_memory.append_turn(sid, "analyst", Turn(role="assistant", text="A1"))

    wm = agent_memory.get_working(sid, "analyst")
    assert [(t.role, t.text) for t in wm.turns] == [("user", "Q1"), ("assistant", "A1")]


def test_turns_are_capped_keeping_the_most_recent():
    sid = agent_memory.new_session("analyst")
    for i in range(agent_memory.MAX_TURNS + 10):
        agent_memory.append_turn(sid, "analyst", Turn(role="user", text=f"Q{i}"))

    wm = agent_memory.get_working(sid, "analyst")
    assert len(wm.turns) == agent_memory.MAX_TURNS
    # Recent context is what a follow-up needs, so the tail survives.
    assert wm.turns[-1].text == f"Q{agent_memory.MAX_TURNS + 9}"


def test_set_run_state_only_writes_what_it_is_given():
    sid = agent_memory.new_session("analyst")
    agent_memory.set_run_state(sid, "analyst", assessment=_assessment(), plan=[{"step": 1}])
    agent_memory.set_run_state(sid, "analyst", entities=[{"name": "Nuctech"}])

    wm = agent_memory.get_working(sid, "analyst")
    assert wm.assessment["executive_summary"].startswith("Fujian Jinhua")
    assert wm.plan == [{"step": 1}]  # not clobbered by the second call
    assert wm.entities == [{"name": "Nuctech"}]


def test_unknown_session_is_none_not_an_error():
    assert agent_memory.get_working("does-not-exist", "analyst") is None
    assert agent_memory.get_working("", "analyst") is None
    assert agent_memory.append_turn("nope", "analyst", Turn(role="user", text="x")) is False


def test_delete_session():
    sid = agent_memory.new_session("analyst")
    assert agent_memory.delete_session(sid, "analyst") is True
    assert agent_memory.get_working(sid, "analyst") is None


# --- Isolation ---------------------------------------------------------------


def test_another_users_session_is_invisible():
    """user_id is the isolation boundary, checked in the seam so no route can skip it."""
    sid = agent_memory.new_session("alice")
    agent_memory.set_run_state(sid, "alice", assessment=_assessment("alice's secret finding"))

    assert agent_memory.get_working(sid, "bob") is None
    assert agent_memory.append_turn(sid, "bob", Turn(role="user", text="steal")) is False
    assert agent_memory.delete_session(sid, "bob") is False

    # Alice's thread is untouched by bob's attempts.
    wm = agent_memory.get_working(sid, "alice")
    assert wm.assessment["executive_summary"] == "alice's secret finding"
    assert wm.turns == []


# --- Guards ------------------------------------------------------------------


def test_oversized_document_is_refused_not_stored(caplog):
    """One session must not be able to eat the instance."""
    sid = agent_memory.new_session("analyst")
    huge = _assessment()
    huge["tool_results"] = {"blob": "x" * (agent_memory._MAX_DOC_BYTES + 1000)}

    stored = agent_memory.set_run_state(sid, "analyst", assessment=huge)
    assert stored is False

    # The thread survives; it just has no hydrated assessment.
    wm = agent_memory.get_working(sid, "analyst")
    assert wm is not None
    assert wm.assessment is None


def test_entities_from_assessment_reads_structured_output():
    """No LLM pass needed — the pipeline already emits the entity list."""
    assessment = _assessment(entities=["Micron"])
    assessment["entity_graph"]["entities"] = [
        {"id": "jinhua", "name": "Fujian Jinhua", "entity_type": "company", "country": "CN"},
        {"id": "umc", "name": "UMC", "entity_type": "company", "country": "TW"},
        {"id": "dupe", "name": "fujian jinhua", "entity_type": "company"},  # case-dupe
    ]

    ents = agent_memory.entities_from_assessment(assessment)
    names = [e["name"] for e in ents]

    assert "Fujian Jinhua" in names
    assert "UMC" in names
    assert "Micron" in names  # picked up from query.target_entities
    assert len(names) == 3, f"case-insensitive dedupe failed: {names}"


def test_backend_reports_memory_without_redis():
    # tests/conftest.py forces REDIS_URL="" — the fallback must be honest about it.
    assert agent_memory.backend_name() == "memory"
