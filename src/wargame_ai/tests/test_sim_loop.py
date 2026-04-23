"""Integration test for the full :class:`SimLoop` with stubbed LLMs and Redis.

Uses a fake Redis implementation if ``fakeredis.aioredis`` is available;
otherwise the test skips Redis assertions and verifies in-memory state only.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

import pytest

from wargame_shared.schemas.sim_event import Domain

from wargame_ai.agents.arbiter import Arbiter
from wargame_ai.agents.country_agent import (
    CountryAgent,
    LLMResponse,
    ToolCall,
)
from wargame_ai.sim.loop import ScenarioSpec, SimLoop, seed_world_from_countries


class ScriptedLLM:
    """Emit a scripted tool-call sequence (one call per act())."""

    def __init__(self, tool_name: str, args: dict[str, Any]) -> None:
        self.tool_name = tool_name
        self.args = args

    async def ainvoke_tools(
        self,
        system_prompt: str,
        human_prompt: str,
        tools: list[dict[str, Any]],
    ) -> LLMResponse:
        return LLMResponse(
            tool_calls=[ToolCall(name=self.tool_name, args=dict(self.args))],
            content="",
        )


class InMemoryRedis:
    """Extremely minimal async Redis stub for capture-only testing."""

    def __init__(self) -> None:
        self.published: list[tuple[str, str]] = []

    async def publish(self, channel: str, message: str) -> int:
        self.published.append((channel, message))
        return 1

    def pubsub(self) -> "InMemoryPubSub":
        return InMemoryPubSub()


class InMemoryPubSub:
    async def subscribe(self, *channels: str) -> None:
        self._channels = channels

    async def unsubscribe(self, *channels: str) -> None:
        pass

    async def close(self) -> None:
        pass

    async def listen(self):  # pragma: no cover — never receives messages in this test
        if False:
            yield  # pragma: no cover
        return


@pytest.fixture()
def world():
    return seed_world_from_countries(
        [
            {"iso3": "CHN", "name": "China", "doctrine": "d", "red_lines": ["r"]},
            {"iso3": "TWN", "name": "Taiwan", "doctrine": "d", "red_lines": ["r"]},
            {"iso3": "USA", "name": "United States", "doctrine": "d", "red_lines": ["r"]},
        ]
    )


async def test_full_loop_runs_and_emits(world) -> None:
    agents = {
        "CHN": CountryAgent(
            "CHN",
            "China",
            "d",
            ["r"],
            llm=ScriptedLLM(
                "economic_action",
                {
                    "target": "TWN",
                    "instrument": "sanction",
                    "magnitude": "broad",
                    "rationale": "Pressure Taiwan.",
                    "estimated_escalation_rung": 2,
                },
            ),
        ),
        "TWN": CountryAgent(
            "TWN",
            "Taiwan",
            "d",
            ["r"],
            llm=ScriptedLLM(
                "diplomatic_action",
                {
                    "target": "USA",
                    "action_type": "alliance_call",
                    "severity": "moderate",
                    "message": "Request consultation.",
                    "rationale": "Seek backing.",
                },
            ),
        ),
        "USA": CountryAgent(
            "USA",
            "United States",
            "d",
            ["r"],
            llm=ScriptedLLM(
                "diplomatic_action",
                {
                    "target": "CHN",
                    "action_type": "formal_protest",
                    "severity": "severe",
                    "message": "Stop immediately.",
                    "rationale": "Support Taiwan.",
                    "estimated_escalation_rung": 1,
                },
            ),
        ),
    }

    arbiter = Arbiter(llm=None)
    redis = InMemoryRedis()

    sim_id = uuid.uuid4()
    scenario = ScenarioSpec(
        id=uuid.uuid4(),
        title="Test",
        description="",
        country_codes=["CHN", "TWN", "USA"],
        initial_conditions={},
        max_turns=3,
    )
    loop = SimLoop(agents=agents, arbiter=arbiter, world=world, memory_store=None, max_turns=3)

    await loop.run(sim_id, scenario, db=None, redis=redis)

    # World advanced to turn 3 (or terminated earlier)
    assert world.turn >= 1
    # Something was published
    assert len(redis.published) > 0

    frame_types = {json.loads(m)["frame_type"] for _, m in redis.published}
    assert "turn_start" in frame_types
    assert "sim_event" in frame_types
    assert "turn_end" in frame_types
    assert "sim_complete" in frame_types

    # Check that trust_score moved on CHN-TWN (CHN sanctioned TWN)
    rel = world.get_relationship("CHN", "TWN")
    assert rel.trust_score < 0


async def test_seed_events_emitted_at_turn_zero(world) -> None:
    agents = {
        code: CountryAgent(
            code,
            code,
            "d",
            [],
            llm=ScriptedLLM("no_action", {"reason": "wait", "rationale": "x"}),
        )
        for code in world.countries
    }
    redis = InMemoryRedis()

    sim_id = uuid.uuid4()
    scenario = ScenarioSpec(
        id=uuid.uuid4(),
        title="Seeded",
        description="",
        country_codes=list(world.countries.keys()),
        initial_conditions={
            "seed_events": [
                {
                    "actor_country": "CHN",
                    "target_country": "TWN",
                    "domain": "kinetic_limited",
                    "action_type": "maritime_quarantine_declaration",
                    "escalation_rung": 3,
                    "payload": {"area": "Taiwan Strait"},
                    "rationale": "Scenario inciting event.",
                }
            ]
        },
        max_turns=2,
    )
    loop = SimLoop(agents=agents, arbiter=Arbiter(llm=None), world=world, max_turns=2)
    await loop.run(sim_id, scenario, db=None, redis=redis)

    # The turn-0 seed event should appear as a sim_event frame
    seed_events = [
        json.loads(m) for _, m in redis.published
        if json.loads(m)["frame_type"] == "sim_event"
    ]
    quarantine = [
        e
        for e in seed_events
        if e["payload"]["event"]["action_type"] == "maritime_quarantine_declaration"
    ]
    assert len(quarantine) == 1
    assert quarantine[0]["payload"]["event"]["turn"] == 0


async def test_abort_stops_loop(world) -> None:
    agents = {
        code: CountryAgent(
            code,
            code,
            "d",
            [],
            llm=ScriptedLLM("no_action", {"reason": "x", "rationale": "y"}),
        )
        for code in world.countries
    }
    redis = InMemoryRedis()
    sim_id = uuid.uuid4()
    scenario = ScenarioSpec(
        id=uuid.uuid4(),
        title="T",
        description="",
        country_codes=list(world.countries.keys()),
        initial_conditions={},
        max_turns=20,
    )
    loop = SimLoop(agents=agents, arbiter=Arbiter(llm=None), world=world, max_turns=20)

    # Pre-abort before running
    loop.abort()
    await loop.run(sim_id, scenario, db=None, redis=redis)

    # The loop should exit immediately and emit a sim_complete(aborted)
    complete = [
        json.loads(m) for _, m in redis.published
        if json.loads(m)["frame_type"] == "sim_complete"
    ]
    assert len(complete) == 1
    assert complete[0]["payload"]["status"] == "aborted"
