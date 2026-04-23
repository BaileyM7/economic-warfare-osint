"""Demo eval harness — the Taiwan 2027 preset scenario.

Runs the full LangGraph sim loop end-to-end with deterministic scripted
LLMs.  Asserts the structural guarantees the frontend relies on:

  * Simulation completes at least 10 turns (or terminates due to rung=5).
  * At least one event emitted per domain (info, diplomatic, economic,
    cyber, kinetic_limited) across the whole trace.
  * No event references an unknown actor / target country.
  * The loop terminates within a bounded wall-clock budget (no infinite loop).
"""

from __future__ import annotations

import asyncio
import json
import random
import time
import uuid
from pathlib import Path
from typing import Any

import pytest
import yaml

from wargame_shared.schemas.sim_event import Domain

from wargame_ai.agents.arbiter import Arbiter
from wargame_ai.agents.country_agent import (
    CountryAgent,
    LLMResponse,
    ToolCall,
)
from wargame_ai.sim.loop import ScenarioSpec, SimLoop, seed_world_from_countries


_SEEDS = Path(__file__).resolve().parents[2] / "shared" / "seeds"


class DeterministicLLM:
    """Rotates through a fixed catalogue of tool calls per country.

    The per-country script guarantees coverage of all five domains across
    the trace so the structural assertions pass.
    """

    #: Pre-scripted "what does country X do in turn T" decisions.  Each entry
    #: yields (tool_name, args) tuples; the index wraps modulo len(script).
    _SCRIPTS: dict[str, list[tuple[str, dict[str, Any]]]] = {
        "CHN": [
            ("information_action", {"channel": "state_media", "target": "TWN", "content_type": "propaganda", "rationale": "Shape narrative domestically."}),
            ("cyber_action", {"target": "TWN", "vector": "ddos", "intent": "disruption", "rationale": "Probe TWN networks."}),
            ("economic_action", {"target": "TWN", "instrument": "export_control", "magnitude": "targeted", "rationale": "Signal costs."}),
            ("kinetic_action", {"target": "TWN", "asset": "Type_055_destroyer", "posture": "show_of_force", "rationale": "Reinforce quarantine."}),
            ("diplomatic_action", {"target": "USA", "action_type": "envoy_summons", "severity": "moderate", "message": "Warning about US interference.", "rationale": "Warn Washington."}),
        ],
        "TWN": [
            ("diplomatic_action", {"target": "USA", "action_type": "alliance_call", "severity": "severe", "message": "Formal request for consultation.", "rationale": "Seek backing."}),
            ("information_action", {"channel": "press_conference", "target": "", "content_type": "public_statement", "rationale": "Rally domestic support."}),
            ("economic_action", {"target": "CHN", "instrument": "export_control", "magnitude": "targeted", "rationale": "Chip leverage."}),
            ("no_action", {"reason": "Observation turn.", "rationale": "Waiting for allied response."}),
        ],
        "USA": [
            ("diplomatic_action", {"target": "CHN", "action_type": "condemnation", "severity": "severe", "message": "Cease immediately.", "rationale": "Verbal deterrence."}),
            ("economic_action", {"target": "CHN", "instrument": "sanction", "magnitude": "broad", "rationale": "Financial pressure."}),
            ("kinetic_action", {"target": "CHN", "asset": "carrier_strike_group", "posture": "show_of_force", "rationale": "Signal commitment."}),
            ("cyber_action", {"target": "CHN", "vector": "network_intrusion", "intent": "espionage", "rationale": "Collect ISR."}),
        ],
        "JPN": [
            ("diplomatic_action", {"target": "USA", "action_type": "alliance_consultation", "severity": "moderate", "message": "Coordinate response.", "rationale": "Align with US."}),
            ("economic_action", {"target": "CHN", "instrument": "export_control", "magnitude": "targeted", "rationale": "Chip tools."}),
            ("no_action", {"reason": "Wait for coalition.", "rationale": "Build coalition."}),
        ],
        "KOR": [
            ("diplomatic_action", {"target": "USA", "action_type": "coordination_call", "severity": "mild", "message": "Discuss.", "rationale": "Hedge."}),
            ("no_action", {"reason": "Avoid entrapment.", "rationale": "Domestic politics."}),
        ],
        "PHL": [
            ("diplomatic_action", {"target": "USA", "action_type": "edca_activation", "severity": "severe", "message": "Hosting US assets.", "rationale": "Treaty obligation."}),
            ("no_action", {"reason": "Defensive only.", "rationale": "Avoid PRC targeting."}),
        ],
        "AUS": [
            ("diplomatic_action", {"target": "USA", "action_type": "aukus_activation", "severity": "moderate", "message": "Aligning.", "rationale": "AUKUS obligation."}),
            ("cyber_action", {"target": "CHN", "vector": "network_intrusion", "intent": "espionage", "rationale": "Five Eyes contribution."}),
            ("no_action", {"reason": "Logistics only.", "rationale": "Support role."}),
        ],
        "PRK": [
            ("information_action", {"channel": "state_media", "target": "KOR", "content_type": "propaganda", "rationale": "Opportunistic noise."}),
            ("kinetic_action", {"target": "KOR", "asset": "ballistic_missile", "posture": "show_of_force", "rationale": "Test launch for leverage."}),
            ("no_action", {"reason": "Save resources.", "rationale": "Conserve."}),
        ],
        "RUS": [
            ("diplomatic_action", {"target": "USA", "action_type": "public_statement", "severity": "mild", "message": "Condemn US provocations.", "rationale": "Support Beijing."}),
            ("cyber_action", {"target": "USA", "vector": "phishing", "intent": "espionage", "rationale": "Opportunistic ISR."}),
            ("no_action", {"reason": "Observe.", "rationale": "Preserve optionality."}),
        ],
        "IND": [
            ("diplomatic_action", {"target": "CHN", "action_type": "public_statement", "severity": "mild", "message": "Call for de-escalation.", "rationale": "Strategic autonomy."}),
            ("no_action", {"reason": "LAC focus.", "rationale": "Watch border."}),
        ],
    }

    def __init__(self, country_code: str) -> None:
        self.country_code = country_code
        self._idx = 0

    async def ainvoke_tools(
        self,
        system_prompt: str,
        human_prompt: str,
        tools: list[dict[str, Any]],
    ) -> LLMResponse:
        script = self._SCRIPTS.get(self.country_code, [("no_action", {"reason": "no script", "rationale": "stub"})])
        name, args = script[self._idx % len(script)]
        self._idx += 1
        # Default required rationale if absent
        args = dict(args)
        args.setdefault("rationale", "Scripted stub rationale.")
        return LLMResponse(
            tool_calls=[ToolCall(name=name, args=args)],
            content="",
        )


class CaptureRedis:
    """In-memory Redis that keeps every published frame."""

    def __init__(self) -> None:
        self.frames: list[dict[str, Any]] = []

    async def publish(self, channel: str, message: str) -> int:
        self.frames.append(json.loads(message))
        return 1

    def pubsub(self) -> Any:
        class _PS:
            async def subscribe(self, *a: Any, **kw: Any) -> None:
                pass

            async def unsubscribe(self, *a: Any, **kw: Any) -> None:
                pass

            async def close(self) -> None:
                pass

            async def listen(self):  # pragma: no cover
                if False:
                    yield

        return _PS()


def _load_yaml(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as fp:
        return yaml.safe_load(fp)


@pytest.fixture()
def taiwan_scenario() -> ScenarioSpec:
    data = _load_yaml(_SEEDS / "taiwan_scenario.yaml")
    return ScenarioSpec(
        id=uuid.uuid4(),
        title=data["title"],
        description=data["description"],
        country_codes=data["country_ids"],
        initial_conditions=data.get("initial_conditions", {}),
        max_turns=int(data.get("max_turns", 10)),
    )


@pytest.fixture()
def world(taiwan_scenario: ScenarioSpec):
    country_seeds = _load_yaml(_SEEDS / "countries.yaml")["countries"]
    filtered = [c for c in country_seeds if c["iso3"] in taiwan_scenario.country_codes]
    return seed_world_from_countries(filtered)


async def test_taiwan_scenario_end_to_end(taiwan_scenario: ScenarioSpec, world) -> None:
    """Run the preset scenario and validate structural properties."""
    random.seed(42)
    agents = {
        code: CountryAgent(
            code=code,
            name=state.name,
            doctrine=state.doctrine,
            red_lines=[r.description for r in state.red_lines],
            llm=DeterministicLLM(code),
        )
        for code, state in world.countries.items()
    }

    redis = CaptureRedis()
    loop = SimLoop(
        agents=agents,
        arbiter=Arbiter(llm=None),
        world=world,
        memory_store=None,
        max_turns=taiwan_scenario.max_turns,
    )

    t0 = time.monotonic()
    sim_id = uuid.uuid4()
    await asyncio.wait_for(
        loop.run(sim_id, taiwan_scenario, db=None, redis=redis),
        timeout=30.0,  # bounded — no infinite loops
    )
    elapsed = time.monotonic() - t0
    assert elapsed < 30.0

    # -------- Assertion 1: at least 10 turns completed OR terminated early
    # on rung=5 (legitimate).  For the scripted fixture, we expect full run.
    assert world.turn >= 10 or any(
        f["payload"].get("peak_escalation_rung", 0) >= 5
        for f in redis.frames
        if f["frame_type"] == "sim_complete"
    )

    # -------- Assertion 2: one event per domain across the whole trace
    sim_events = [
        f["payload"]["event"]
        for f in redis.frames
        if f["frame_type"] == "sim_event"
    ]
    assert sim_events, "no sim_events emitted at all"
    domains_seen = {e["domain"] for e in sim_events}
    required = {
        Domain.info.value,
        Domain.diplomatic.value,
        Domain.economic.value,
        Domain.cyber.value,
        Domain.kinetic_limited.value,
    }
    missing = required - domains_seen
    assert not missing, f"missing domains: {missing}; saw: {domains_seen}"

    # -------- Assertion 3: every actor/target is a known country
    known = set(taiwan_scenario.country_codes)
    for e in sim_events:
        assert e["actor_country"] in known, f"unknown actor: {e['actor_country']}"
        if e["target_country"] is not None:
            assert e["target_country"] in known, f"unknown target: {e['target_country']}"

    # -------- Assertion 4: sim_complete emitted exactly once
    completions = [f for f in redis.frames if f["frame_type"] == "sim_complete"]
    assert len(completions) == 1
    assert completions[0]["payload"]["status"] in ("completed", "aborted")
