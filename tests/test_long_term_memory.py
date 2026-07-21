"""Long-term memory (Phase 5): poisoning guards, dedup, isolation, recall injection.

The load-bearing tests:
  * `test_fabricated_source_fact_is_dropped` — the anti-poisoning guard. An
    extracted "fact" that cites a source the analysis never used must NOT be
    stored, or one hallucinated Monday fact steers every later plan.
  * `test_recalled_memory_reaches_the_decompose_prompt` — the whole point of
    pre-decompose recall: memory changes the PLAN, not just the prose. Asserted on
    the prompt (deterministic), not the model's output.
  * `test_memories_are_per_user` — memory is scoped to the analyst.

All run on the default hermetic config (no Redis, no key), so they also prove the
lexical fallback works — which is what prod runs until Redis 8 is live.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.common import agent_memory
from src.common.agent_memory import Memory


@pytest.fixture(autouse=True)
def _clean_db(app_client):
    """app_client wipes + re-inits the throwaway DB per test (fresh memories)."""
    agent_memory.reset_for_tests()
    yield
    agent_memory.reset_for_tests()


def _assessment(summary="Fujian Jinhua is on the BIS Entity List.", sources=None, entities=None):
    return {
        "executive_summary": summary,
        "query": {"raw_query": "sanction Fujian Jinhua", "target_entities": ["Fujian Jinhua"]},
        "findings": [{"category": "sanctions", "finding": "Entity List", "confidence": "HIGH"}],
        "friendly_fire": [],
        "entity_graph": {"entities": entities or [], "relationships": []},
        "sources": sources if sources is not None else [{"name": "OFAC SDN"}, {"name": "Sayari"}],
        "tool_results": {},
    }


def _mock_client(items):
    client = MagicMock()
    client.messages.create = AsyncMock(
        return_value=MagicMock(content=[MagicMock(text=json.dumps(items))])
    )
    return client


# --- The store ---------------------------------------------------------------


def test_remember_and_list_round_trip():
    n = agent_memory.remember(
        "alice",
        [
            Memory(
                text="Fujian Jinhua is on the BIS Entity List.",
                entity_names=["Fujian Jinhua"],
                confidence="HIGH",
                sources=[{"name": "OFAC SDN"}],
            )
        ],
    )
    assert n == 1
    mems = agent_memory.list_memories("alice")
    assert len(mems) == 1
    assert mems[0]["entity_names"] == ["fujian jinhua"]  # normalized lowercase


def test_exact_duplicate_reinforces_not_duplicates():
    m = Memory(
        text="Fujian Jinhua is on the BIS Entity List.",
        entity_names=["Fujian Jinhua"],
        confidence="HIGH",
        sources=[{"name": "OFAC SDN"}],
    )
    agent_memory.remember("alice", [m])
    # Same normalized text, different source → reinforce, union sources.
    again = Memory(
        text="fujian  jinhua is on the BIS entity list.",
        entity_names=["Fujian Jinhua"],
        confidence="MEDIUM",
        sources=[{"name": "Trade.gov CSL"}],
    )
    n = agent_memory.remember("alice", [again])

    assert n == 0  # nothing new inserted
    mems = agent_memory.list_memories("alice")
    assert len(mems) == 1
    assert mems[0]["seen_count"] == 2
    assert {s["name"] for s in mems[0]["sources"]} == {"OFAC SDN", "Trade.gov CSL"}
    assert mems[0]["confidence"] == "HIGH"  # keeps the stronger


def test_memories_are_per_user():
    agent_memory.remember(
        "alice", [Memory(text="alice's private finding about X.", entity_names=["X"])]
    )

    assert agent_memory.list_memories("bob") == []
    assert agent_memory.memory_backend_name() == "lexical"  # no redis in tests

    # bob can't delete alice's memory
    alice_mem = agent_memory.list_memories("alice")[0]
    assert agent_memory.forget(alice_mem["memory_id"], "bob") is False
    assert agent_memory.forget(alice_mem["memory_id"], "alice") is True


@pytest.mark.asyncio
async def test_lexical_recall_finds_relevant_memories():
    agent_memory.remember(
        "alice",
        [
            Memory(
                text="Fujian Jinhua's DRAM line came from UMC.",
                entity_names=["Fujian Jinhua", "UMC"],
            ),
            Memory(text="Maersk operates container shipping routes.", entity_names=["Maersk"]),
        ],
    )
    hits = await agent_memory.recall("what supplied the DRAM fabrication", user_id="alice", k=2)
    assert hits, "lexical recall should still return matches without Redis"
    assert "DRAM" in hits[0][0]["text"]


@pytest.mark.asyncio
async def test_recall_is_scoped_to_the_user():
    agent_memory.remember(
        "alice", [Memory(text="Fujian Jinhua on the Entity List.", entity_names=["Fujian Jinhua"])]
    )
    assert await agent_memory.recall("Fujian Jinhua", user_id="bob") == []


# --- Extraction + the poisoning guard ----------------------------------------


@pytest.mark.asyncio
async def test_fabricated_source_fact_is_dropped(monkeypatch):
    """A fact citing a source the analysis never used must not be stored."""
    import src.orchestrator.memory_extract as mx

    items = [
        {
            "text": "Fujian Jinhua is on the BIS Entity List.",
            "memory_type": "fact",
            "topics": ["sanctions"],
            "entity_names": ["Fujian Jinhua"],
            "confidence": "HIGH",
            "sources": ["OFAC SDN"],
        },  # grounded — kept
        {
            "text": "Fujian Jinhua secretly owns a dark shipping fleet.",
            "memory_type": "fact",
            "topics": ["maritime"],
            "entity_names": ["Fujian Jinhua"],
            "confidence": "HIGH",
            "sources": ["Totally Made Up Report"],
        },  # ungrounded — dropped
    ]
    monkeypatch.setattr(mx, "get_anthropic_client", lambda: _mock_client(items))

    kept = await mx.extract_memories(
        query="sanction Fujian Jinhua",
        assessment=_assessment(),  # real sources: OFAC SDN, Sayari
        user_id="alice",
        session_id="s1",
        analysis_id="a1",
    )
    texts = [m.text for m in kept]
    assert any("Entity List" in t for t in texts)
    assert not any("shipping fleet" in t for t in texts), "fabricated-source fact leaked"


@pytest.mark.asyncio
async def test_preferences_are_exempt_from_the_source_guard(monkeypatch):
    """A 'preference' is about the analyst, not a sourced claim — allowed unsourced."""
    import src.orchestrator.memory_extract as mx

    items = [
        {
            "text": "Analyst tracks PRC semiconductor supply chains.",
            "memory_type": "preference",
            "topics": ["analyst_interest"],
            "entity_names": [],
            "confidence": "MEDIUM",
            "sources": [],
        }
    ]
    monkeypatch.setattr(mx, "get_anthropic_client", lambda: _mock_client(items))

    kept = await mx.extract_memories(
        query="sanction Fujian Jinhua",
        assessment=_assessment(),
        user_id="alice",
        session_id="s1",
        analysis_id="a1",
    )
    assert len(kept) == 1 and kept[0].memory_type == "preference"


@pytest.mark.asyncio
async def test_extraction_needs_real_sources(monkeypatch):
    """An assessment with no grounded sources yields no memories at all."""
    import src.orchestrator.memory_extract as mx

    monkeypatch.setattr(mx, "get_anthropic_client", lambda: _mock_client([{"text": "x"}]))
    kept = await mx.extract_memories(
        query="q",
        assessment=_assessment(sources=[]),
        user_id="alice",
        session_id="s1",
        analysis_id="a1",
    )
    assert kept == []


@pytest.mark.asyncio
async def test_extraction_resolves_entity_ids_from_the_graph(monkeypatch):
    import src.orchestrator.memory_extract as mx

    items = [
        {
            "text": "Fujian Jinhua is on the BIS Entity List.",
            "memory_type": "fact",
            "topics": ["sanctions"],
            "entity_names": ["Fujian Jinhua"],
            "confidence": "HIGH",
            "sources": ["OFAC SDN"],
        }
    ]
    monkeypatch.setattr(mx, "get_anthropic_client", lambda: _mock_client(items))

    assessment = _assessment(
        entities=[{"id": "jinhua", "name": "Fujian Jinhua", "entity_type": "company"}]
    )
    kept = await mx.extract_memories(
        query="q", assessment=assessment, user_id="alice", session_id="s1", analysis_id="a1"
    )
    assert kept[0].entity_ids == ["jinhua"]


def test_array_parser_handles_fenced_and_bare_json():
    from src.orchestrator.memory_extract import _parse_items

    arr = '[{"text": "a"}, {"text": "b"}]'
    assert len(_parse_items(arr)) == 2
    assert len(_parse_items(f"```json\n{arr}\n```")) == 2
    assert len(_parse_items(f"Here you go:\n{arr}\nDone.")) == 2
    assert _parse_items("not json at all") == []


# --- Recall injection into the plan (the demo, deterministically) ------------


@pytest.mark.asyncio
async def test_recalled_memory_reaches_the_decompose_prompt():
    """Pre-decompose recall must put prior facts into the DECOMPOSE prompt.

    That's what lets Monday's findings change Friday's plan. Assert on the prompt
    the model receives, not on the model's output.
    """
    from src.orchestrator.main import Orchestrator

    orch = Orchestrator.__new__(Orchestrator)  # skip __init__ (needs an API key)

    captured = {}

    class _Provider:
        async def complete(self, *, system, messages, max_tokens, model):
            captured["user_content"] = messages[0]["content"]
            return "[]"  # empty plan; we only care about the prompt

    orch.provider = _Provider()
    orch.decompose_model = "haiku"

    memory = [
        (
            {
                "text": "Fujian Jinhua's DRAM line came from UMC.",
                "confidence": "MEDIUM",
                "entity_names": ["fujian jinhua", "umc"],
                "sources": [{"name": "Sayari"}],
            },
            0.91,
        ),
    ]
    await orch._decompose("what else is exposed to that supply chain?", memory=memory)

    prompt = captured["user_content"]
    assert "UNVERIFIED prior work" in prompt  # the labeling guard
    assert "Fujian Jinhua's DRAM line came from UMC." in prompt  # the fact itself
    assert "Sayari" in prompt  # provenance travels


@pytest.mark.asyncio
async def test_no_memory_leaves_the_prompt_unchanged():
    from src.orchestrator.main import Orchestrator

    orch = Orchestrator.__new__(Orchestrator)
    captured = {}

    class _Provider:
        async def complete(self, *, system, messages, max_tokens, model):
            captured["c"] = messages[0]["content"]
            return "[]"

    orch.provider = _Provider()
    orch.decompose_model = "haiku"

    await orch._decompose("a fresh question", memory=[])
    assert "UNVERIFIED" not in captured["c"]  # nothing injected when there's no memory


@pytest.mark.asyncio
async def test_recall_merges_bare_and_entity_anchored_prongs(monkeypatch):
    """_recall unions a bare-query pass with an entity-anchored pass.

    The entity-anchored pass (using the thread's working-memory entities) is what
    resolves anaphora like "that supply chain" — so a memory that only the anchored
    pass finds must still make it into the merged result, with its higher score.
    """
    from src.orchestrator.main import Orchestrator

    orch = Orchestrator.__new__(Orchestrator)

    bare = [({"memory_id": "m1", "text": "generic supply chain fact"}, 0.40)]
    anchored = [({"memory_id": "m2", "text": "Fujian Jinhua DRAM fact"}, 0.88)]

    async def _fake_recall(query, *, user_id, k=8, entity_names=None, memory_types=None):
        return anchored if entity_names else bare

    monkeypatch.setattr(agent_memory, "recall", _fake_recall)
    # The thread knows it's about Fujian Jinhua -> triggers the anchored pass.
    monkeypatch.setattr(
        orch, "_session_entities", lambda sid, uid: ["Fujian Jinhua"], raising=False
    )

    hits = await orch._recall(
        "what else is exposed to that supply chain?", "alice", "s1", lambda _m: None
    )
    ids = [m["memory_id"] for m, _ in hits]

    assert "m2" in ids, "the entity-anchored memory must be included"
    assert "m1" in ids, "the bare-query memory must also be included"
    assert ids[0] == "m2", "the higher-scored anchored hit should rank first"


@pytest.mark.asyncio
async def test_recall_returns_empty_without_a_user():
    from src.orchestrator.main import Orchestrator

    orch = Orchestrator.__new__(Orchestrator)
    assert await orch._recall("q", None, "s1", lambda _m: None) == []


def test_format_memory_block_is_empty_without_memory():
    from src.orchestrator.main import _format_memory_block

    assert _format_memory_block(None) == ""
    assert _format_memory_block([]) == ""
