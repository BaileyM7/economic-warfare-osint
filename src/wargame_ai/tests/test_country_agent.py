"""Unit tests for :mod:`ai.agents.country_agent`."""

from __future__ import annotations

from typing import Any

import pytest

from wargame_shared.schemas.sim_event import Domain

from wargame_ai.agents.country_agent import (
    COUNTRY_AGENT_TOOLS,
    CountryAgent,
    LLMResponse,
    MemoryRecord,
    Perception,
    ToolCall,
    render_country_prompt,
    tool_call_to_action,
)
from wargame_ai.agents.leader_profile import LeaderProfile, OceanScores


class StubClient:
    """Fake LLM client that returns a canned tool call."""

    def __init__(self, tool_call: ToolCall | None, content: str = "") -> None:
        self.tool_call = tool_call
        self.content = content
        self.last_system_prompt: str = ""
        self.last_human_prompt: str = ""
        self.last_tools: list[dict[str, Any]] = []

    async def ainvoke_tools(
        self,
        system_prompt: str,
        human_prompt: str,
        tools: list[dict[str, Any]],
    ) -> LLMResponse:
        self.last_system_prompt = system_prompt
        self.last_human_prompt = human_prompt
        self.last_tools = tools
        return LLMResponse(
            tool_calls=[self.tool_call] if self.tool_call else [],
            content=self.content,
        )


def _make_perception() -> Perception:
    return Perception(
        country_iso3="CHN",
        country_name="China",
        doctrine="Preserve the party.",
        red_lines=["Taiwan independence"],
        current_posture={"diplomatic": "aggressive"},
        resource_budget={"diplomatic": 80, "economic": 90, "military": 95, "cyber": 70, "information": 85},
        world_view={"turn": 4, "self": None, "others": {}, "relationships": {}, "recent_events_involving_me": [], "active_crises": []},
    )


class TestPromptRendering:
    def test_all_placeholders_substituted(self) -> None:
        rendered = render_country_prompt(_make_perception(), [])
        assert "{country_name}" not in rendered
        assert "{doctrine}" not in rendered
        assert "{persona}" not in rendered
        assert "China" in rendered
        assert "Taiwan independence" in rendered
        # The user-TODO marker should still be present (agent still runs)
        assert "TODO(user)" in rendered

    def test_memories_rendered(self) -> None:
        mem = MemoryRecord(content="USA deployed carrier strike group.", turn=2)
        rendered = render_country_prompt(_make_perception(), [mem])
        assert "USA deployed carrier strike group" in rendered

    def test_persona_rendered_into_leadership_section(self) -> None:
        perception = Perception(
            country_iso3="CHN",
            country_name="China",
            doctrine="Preserve the party.",
            red_lines=["Taiwan independence"],
            current_posture={"diplomatic": "aggressive"},
            resource_budget={"diplomatic": 80, "economic": 90, "military": 95, "cyber": 70, "information": 85},
            world_view={"turn": 4, "self": None, "others": {}, "relationships": {}, "recent_events_involving_me": [], "active_crises": []},
            persona="**Leader:** Xi Jinping. Patient on kinetic; aggressive on gray-zone.",
        )
        rendered = render_country_prompt(perception, [])
        # Persona content appears verbatim
        assert "Xi Jinping" in rendered
        assert "Patient on kinetic" in rendered
        # Section header is present so the model sees it as dedicated context
        assert "Leadership & decision style" in rendered
        # Persona sits above posture/budget (stable region for prompt caching)
        assert rendered.index("Leadership & decision style") < rendered.index("Your current posture")

    def test_persona_absent_falls_back_gracefully(self) -> None:
        # Default Perception has persona="" — must not leave an unsubstituted token
        rendered = render_country_prompt(_make_perception(), [])
        assert "{persona}" not in rendered
        assert "no persona authored" in rendered

    def test_leader_profile_rendered_into_prompt(self) -> None:
        profile = LeaderProfile(
            leader="Xi Jinping",
            ocean=OceanScores(
                openness=35,
                conscientiousness=80,
                extraversion=30,
                agreeableness=25,
                neuroticism=55,
            ),
            ocean_descriptors={
                "openness": "Suspicious of novel frameworks.",
                "conscientiousness": "Lets situations ripen.",
                "extraversion": "Prefers back-channels.",
                "agreeableness": "Punishes slights coldly.",
                "neuroticism": "Volatile when legitimacy is touched.",
            },
        )
        perception = Perception(
            country_iso3="CHN",
            country_name="China",
            doctrine="Preserve the party.",
            red_lines=["Taiwan independence"],
            current_posture={"diplomatic": "aggressive"},
            resource_budget={"diplomatic": 80, "economic": 90, "military": 95, "cyber": 70, "information": 85},
            world_view={"turn": 4, "self": None, "others": {}, "relationships": {}, "recent_events_involving_me": [], "active_crises": []},
            persona="**Leader:** Xi Jinping. Patient on kinetic; aggressive on gray-zone.",
            leader_profile=profile,
        )
        rendered = render_country_prompt(perception, [])
        # Token must be substituted
        assert "{leader_profile}" not in rendered
        # All five scores appear with their integer values
        for value in ("35", "80", "30", "25", "55"):
            assert f"| {value} |" in rendered
        # Authored descriptors appear verbatim
        assert "Suspicious of novel frameworks." in rendered
        assert "Volatile when legitimacy is touched." in rendered
        # OCEAN block is positioned above the legacy "Leadership & decision style" block
        ocean_idx = rendered.index("Leader profile (Big Five / OCEAN)")
        legacy_idx = rendered.index("Leadership & decision style")
        assert ocean_idx < legacy_idx

    def test_leader_profile_absent_renders_placeholder(self) -> None:
        # Default Perception has leader_profile=None — block falls back gracefully
        rendered = render_country_prompt(_make_perception(), [])
        assert "{leader_profile}" not in rendered
        assert "no structured leader profile" in rendered


class TestPromptCacheSplit:
    def test_split_puts_stable_content_before_boundary(self) -> None:
        from wargame_ai.agents.country_agent import (
            _PROMPT_CACHE_BOUNDARY,
            _split_for_cache,
        )

        rendered = render_country_prompt(_make_perception(), [])
        cacheable, volatile = _split_for_cache(rendered)

        # The cacheable prefix must contain the leader-profile / persona /
        # doctrine blocks — everything that's identical turn-to-turn.
        assert "Leader profile (Big Five / OCEAN)" in cacheable
        assert "Leadership & decision style" in cacheable
        assert "doctrine" in cacheable.lower()

        # The volatile suffix must START with the intelligence header (the
        # boundary lives in the volatile half so the header always renders
        # fresh at the top of the per-turn content).
        assert volatile.lstrip().startswith(_PROMPT_CACHE_BOUNDARY)

        # The intel / posture / budget / memory / world-view blocks must
        # NOT appear in the cacheable prefix — they change every turn.
        assert "Your current posture" not in cacheable
        assert "Your resource budget" not in cacheable
        assert "What you perceive this turn" not in cacheable

    def test_split_missing_boundary_is_all_stable(self) -> None:
        from wargame_ai.agents.country_agent import _split_for_cache

        text = "some prompt with no boundary marker"
        cacheable, volatile = _split_for_cache(text)
        assert cacheable.strip() == text
        assert volatile == ""


class TestToolCallParsing:
    def test_diplomatic_action_parsed(self) -> None:
        call = ToolCall(
            name="diplomatic_action",
            args={
                "target": "TWN",
                "action_type": "formal_protest",
                "severity": "severe",
                "message": "We strongly condemn ...",
                "rationale": "Test rationale.",
                "estimated_escalation_rung": 2,
            },
        )
        action = tool_call_to_action("CHN", call)
        assert action.actor == "CHN"
        assert action.target == "TWN"
        assert action.domain is Domain.diplomatic
        assert action.action_type == "formal_protest"
        assert action.estimated_escalation_rung == 2
        assert "strongly" in action.payload["message"]

    def test_kinetic_major_strike_maps_to_kinetic_general(self) -> None:
        call = ToolCall(
            name="kinetic_action",
            args={
                "target": "TWN",
                "asset": "DF-17 missiles",
                "posture": "major_strike",
                "rationale": "...",
                "estimated_escalation_rung": 5,
            },
        )
        action = tool_call_to_action("CHN", call)
        assert action.domain is Domain.kinetic_general

    def test_kinetic_show_of_force_is_kinetic_limited(self) -> None:
        call = ToolCall(
            name="kinetic_action",
            args={
                "target": "TWN",
                "asset": "carrier",
                "posture": "show_of_force",
                "rationale": "...",
            },
        )
        action = tool_call_to_action("CHN", call)
        assert action.domain is Domain.kinetic_limited

    def test_no_action_parsed(self) -> None:
        call = ToolCall(
            name="no_action",
            args={"reason": "Observation phase.", "rationale": "Not enough info yet."},
        )
        action = tool_call_to_action("CHN", call)
        assert action.action_type == "no_action"
        assert action.target is None

    def test_unknown_tool_raises(self) -> None:
        with pytest.raises(ValueError):
            tool_call_to_action("CHN", ToolCall(name="unknown_tool", args={}))


class TestCountryAgent:
    async def test_valid_code_required(self) -> None:
        with pytest.raises(ValueError):
            CountryAgent(
                code="CH",
                name="x",
                doctrine="",
                red_lines=[],
                llm=StubClient(None),
            )

    async def test_act_returns_proposed_action(self) -> None:
        tool_call = ToolCall(
            name="economic_action",
            args={
                "target": "TWN",
                "instrument": "sanction",
                "magnitude": "severe",
                "rationale": "Economic pressure phase.",
                "estimated_escalation_rung": 3,
            },
        )
        client = StubClient(tool_call)
        agent = CountryAgent("CHN", "China", "doctrine", ["rl"], llm=client)
        action = await agent.act(_make_perception(), [])
        assert action.actor == "CHN"
        assert action.target == "TWN"
        assert action.domain is Domain.economic

    async def test_act_falls_back_to_no_action_on_empty_tool_calls(self) -> None:
        client = StubClient(None, content="I declined to act this turn.")
        agent = CountryAgent("CHN", "China", "", [], llm=client)
        action = await agent.act(_make_perception(), [])
        assert action.action_type == "no_action"

    async def test_act_prevents_self_targeting(self) -> None:
        tool_call = ToolCall(
            name="diplomatic_action",
            args={
                "target": "CHN",  # self-target
                "action_type": "statement",
                "severity": "mild",
                "message": "x",
                "rationale": "oops",
            },
        )
        client = StubClient(tool_call)
        agent = CountryAgent("CHN", "China", "", [], llm=client)
        action = await agent.act(_make_perception(), [])
        assert action.target is None  # stripped

    async def test_tools_bound_match_schema(self) -> None:
        """The agent must pass the full tool list to the LLM."""
        client = StubClient(None)
        agent = CountryAgent("CHN", "China", "", [], llm=client)
        await agent.act(_make_perception(), [])
        tool_names = {t["name"] for t in client.last_tools}
        assert tool_names == {
            "diplomatic_action",
            "economic_action",
            "information_action",
            "cyber_action",
            "kinetic_action",
            "no_action",
        }
        # Expected set matches the exported constant
        assert {t["name"] for t in COUNTRY_AGENT_TOOLS} == tool_names
