"""Tests for :mod:`ai.agents.arbiter`."""

from __future__ import annotations

from typing import Any

import pytest

from wargame_shared.schemas.sim_event import Domain

from wargame_ai.agents.arbiter import Arbiter
from wargame_ai.sim.world import (
    CountryState,
    ProposedAction,
    ResolvedOutcome,
    WorldState,
)


def _world(codes: list[str] = ["CHN", "TWN", "USA"]) -> WorldState:
    w = WorldState()
    for c in codes:
        w.countries[c] = CountryState(iso3=c, name=c)
    return w


class TestHeuristicResolver:
    async def test_empty_proposals_returns_empty(self) -> None:
        arb = Arbiter(llm=None)
        out = await arb.resolve([], _world())
        assert out == []

    async def test_unknown_actor_rejected(self) -> None:
        arb = Arbiter(llm=None)
        p = ProposedAction(
            actor="ZZZ",
            target="TWN",
            domain=Domain.info,
            action_type="leak",
        )
        out = await arb.resolve([p], _world())
        assert len(out) == 1
        assert out[0].outcome is ResolvedOutcome.rejected
        assert "unknown actor" in out[0].arbiter_note

    async def test_self_target_rejected(self) -> None:
        arb = Arbiter(llm=None)
        # ProposedAction allows target=actor; arbiter should reject it.
        p = ProposedAction(
            actor="CHN",
            target="CHN",
            domain=Domain.diplomatic,
            action_type="x",
        )
        # Bypass the Pydantic validator by constructing via model_construct
        p2 = ProposedAction.model_construct(
            actor="CHN",
            target="CHN",
            domain=Domain.diplomatic,
            action_type="x",
            payload={},
            rationale="",
            estimated_escalation_rung=0,
        )
        out = await arb.resolve([p2], _world())
        assert out[0].outcome is ResolvedOutcome.rejected

    async def test_sequencing_info_cyber_first(self) -> None:
        arb = Arbiter(llm=None)
        actions = [
            ProposedAction(
                actor="CHN",
                target="TWN",
                domain=Domain.kinetic_limited,
                action_type="blockade",
            ),
            ProposedAction(
                actor="USA",
                target="CHN",
                domain=Domain.cyber,
                action_type="intrusion",
            ),
            ProposedAction(
                actor="TWN",
                target="CHN",
                domain=Domain.diplomatic,
                action_type="protest",
            ),
        ]
        out = await arb.resolve(actions, _world())
        # Sequence order: cyber (0) < diplomatic (1) < kinetic (2)
        seq_by_domain = {r.proposed.domain: r.sequence_index for r in out}
        assert seq_by_domain[Domain.cyber] < seq_by_domain[Domain.diplomatic]
        assert seq_by_domain[Domain.diplomatic] < seq_by_domain[Domain.kinetic_limited]

    async def test_trust_deltas_scale_with_rung(self) -> None:
        arb = Arbiter(llm=None)
        high = ProposedAction(
            actor="CHN",
            target="TWN",
            domain=Domain.kinetic_limited,
            action_type="strike",
        )
        out = await arb.resolve([high], _world())
        assert out[0].trust_delta <= -10  # escalation hurts trust


class TestLLMRefinement:
    async def test_llm_output_is_applied(self) -> None:
        class StubLLM:
            async def ainvoke_json(
                self, system_prompt: str, human_prompt: str
            ) -> list[dict[str, Any]]:
                return [
                    {
                        "proposed_index": 0,
                        "outcome": "accepted",
                        "final_escalation_rung": 4,
                        "arbiter_note": "LLM override",
                        "sequence_index": 7,
                        "trust_delta": -33,
                        "hostility_delta": 22,
                    }
                ]

        arb = Arbiter(llm=StubLLM())
        p = ProposedAction(
            actor="CHN",
            target="TWN",
            domain=Domain.economic,
            action_type="sanction",
        )
        out = await arb.resolve([p], _world())
        assert out[0].final_escalation_rung == 4
        assert out[0].arbiter_note == "LLM override"
        assert out[0].trust_delta == -33

    async def test_llm_failure_falls_back_to_heuristic(self) -> None:
        class BrokenLLM:
            async def ainvoke_json(self, *a: Any, **kw: Any) -> list[dict[str, Any]]:
                raise RuntimeError("rate limit")

        arb = Arbiter(llm=BrokenLLM())
        p = ProposedAction(
            actor="CHN",
            target="TWN",
            domain=Domain.info,
            action_type="leak",
        )
        out = await arb.resolve([p], _world())
        assert len(out) == 1  # did not crash
        assert out[0].outcome is ResolvedOutcome.accepted
