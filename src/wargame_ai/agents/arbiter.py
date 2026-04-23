"""Arbiter — conflict adjudicator for simultaneous agent actions.

The arbiter consumes all :class:`ProposedAction` objects from a single turn,
produces :class:`ResolvedAction` objects, and returns them in temporal order.

It is expected to be a Claude Opus call in production (higher reasoning
capacity for tricky conflicts).  A stub client shape is supported for tests.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Protocol

import structlog

from wargame_ai.sim.escalation_ladder import classify_action
from wargame_ai.sim.world import (
    ProposedAction,
    ResolvedAction,
    ResolvedOutcome,
    WorldState,
)

log = structlog.get_logger(__name__)


_PROMPT_PATH = Path(__file__).parent / "prompts" / "arbiter.md"


def _load_prompt() -> str:
    """Load the arbiter system prompt from disk."""
    return _PROMPT_PATH.read_text(encoding="utf-8")


class ArbiterLLMClient(Protocol):
    """LLM client interface for the arbiter (mirrors country-agent LLM client)."""

    async def ainvoke_json(
        self,
        system_prompt: str,
        human_prompt: str,
    ) -> list[dict[str, Any]]:  # pragma: no cover — protocol only
        """Return a list of per-proposal resolution dicts, in input order."""
        ...


class Arbiter:
    """Adjudicates, merges, and sequences a turn's proposed actions."""

    def __init__(self, llm: ArbiterLLMClient | None = None) -> None:
        """
        Args:
            llm: Optional LLM client.  If ``None``, the arbiter falls back to a
                pure heuristic resolver — useful for tests and for development
                without an API key.
        """
        self.llm = llm
        self.system_prompt = _load_prompt()

    # ------------------------------------------------------------------ #
    # Public entry point                                                   #
    # ------------------------------------------------------------------ #

    async def resolve(
        self,
        proposed: list[ProposedAction],
        world: WorldState,
    ) -> list[ResolvedAction]:
        """Resolve all proposals for a turn.

        Strategy:
          1. Apply fast, deterministic validation (self-target, unknown actor).
          2. If an LLM client is configured, ask it to finalise rungs/deltas
             and sequence the actions.  If the LLM fails or is unavailable,
             fall back to the heuristic resolver.

        Args:
            proposed: All ProposedActions from all agents this turn.
            world: Current world state (used for context and validation).

        Returns:
            A list of ResolvedAction objects in temporal sequence order.
        """
        if not proposed:
            return []

        # ------------------------------ deterministic pre-pass
        pre_resolved: list[ResolvedAction] = []
        for action in proposed:
            outcome = ResolvedOutcome.accepted
            note = ""
            if action.actor not in world.countries:
                outcome = ResolvedOutcome.rejected
                note = f"unknown actor: {action.actor}"
            elif action.target is not None and action.target not in world.countries:
                outcome = ResolvedOutcome.rejected
                note = f"unknown target: {action.target}"
            elif action.target == action.actor:
                outcome = ResolvedOutcome.rejected
                note = "actor cannot target itself"

            rung = int(classify_action(action))
            pre_resolved.append(
                ResolvedAction(
                    proposed=action,
                    outcome=outcome,
                    final_escalation_rung=rung,
                    arbiter_note=note,
                    sequence_index=0,
                    trust_delta=0,
                    hostility_delta=0,
                )
            )

        # ------------------------------ optionally enrich via LLM
        if self.llm is not None:
            try:
                pre_resolved = await self._llm_refine(pre_resolved, world)
            except Exception as exc:  # noqa: BLE001 — resilience over purity
                log.warning("arbiter_llm_failed_fallback", error=str(exc))

        # ------------------------------ heuristic sequencing + deltas
        return self._heuristic_finalise(pre_resolved)

    # ------------------------------------------------------------------ #
    # Heuristic sequencing / deltas                                         #
    # ------------------------------------------------------------------ #

    _DOMAIN_TIER: dict[str, int] = {
        "info": 0,
        "cyber": 0,
        "diplomatic": 1,
        "economic": 1,
        "kinetic_limited": 2,
        "kinetic_general": 2,
    }

    def _heuristic_finalise(self, resolved: list[ResolvedAction]) -> list[ResolvedAction]:
        """Order by domain tier; assign trust/hostility deltas from rung.

        Overwrites fields only where they are still at their defaults — so
        an LLM pass that populated deltas/sequence is preserved.
        """
        # Deterministic sort: tier, then negative rung (bigger first within tier),
        # then actor code for stability.
        ordered = sorted(
            resolved,
            key=lambda r: (
                self._DOMAIN_TIER.get(r.proposed.domain.value, 1),
                -r.final_escalation_rung,
                r.proposed.actor,
            ),
        )

        for idx, r in enumerate(ordered):
            if r.sequence_index == 0:
                r.sequence_index = idx

            if r.outcome is not ResolvedOutcome.accepted:
                continue

            # Default trust/hostility deltas scaled by rung
            if r.trust_delta == 0 and r.hostility_delta == 0:
                rung = r.final_escalation_rung
                if r.proposed.action_type == "no_action":
                    r.trust_delta = 0
                    r.hostility_delta = 0
                elif rung <= 1:
                    r.trust_delta = -2
                    r.hostility_delta = 1
                elif rung == 2:
                    r.trust_delta = -8
                    r.hostility_delta = 5
                elif rung == 3:
                    r.trust_delta = -15
                    r.hostility_delta = 12
                elif rung == 4:
                    r.trust_delta = -25
                    r.hostility_delta = 20
                else:  # rung == 5
                    r.trust_delta = -40
                    r.hostility_delta = 35

        return ordered

    # ------------------------------------------------------------------ #
    # LLM refinement                                                        #
    # ------------------------------------------------------------------ #

    async def _llm_refine(
        self,
        pre_resolved: list[ResolvedAction],
        world: WorldState,
    ) -> list[ResolvedAction]:
        """Call the LLM to refine rungs / deltas / sequence.

        The LLM returns a list of dicts (one per input proposal) with the keys
        documented in the arbiter prompt.  Any field that is missing falls
        back to the heuristic default.
        """
        assert self.llm is not None

        proposals_json = [
            {
                "index": i,
                "actor": r.proposed.actor,
                "target": r.proposed.target,
                "domain": r.proposed.domain.value,
                "action_type": r.proposed.action_type,
                "payload": r.proposed.payload,
                "rationale": r.proposed.rationale,
                "estimated_rung": r.proposed.estimated_escalation_rung,
                "current_outcome": r.outcome.value,
                "heuristic_rung": r.final_escalation_rung,
                "heuristic_note": r.arbiter_note,
            }
            for i, r in enumerate(pre_resolved)
        ]

        human_prompt = json.dumps(
            {
                "turn": world.turn,
                "world_snapshot": {
                    "countries": list(world.countries.keys()),
                    "active_crises": [c.model_dump(mode="json") for c in world.active_crises],
                },
                "proposals": proposals_json,
            },
            indent=2,
            default=str,
        )

        raw = await self.llm.ainvoke_json(
            system_prompt=self.system_prompt,
            human_prompt=human_prompt,
        )

        # Build a lookup by proposed_index
        by_idx = {int(d.get("proposed_index", -1)): d for d in raw if isinstance(d, dict)}
        for i, r in enumerate(pre_resolved):
            d = by_idx.get(i)
            if d is None:
                continue
            outcome_str = str(d.get("outcome", r.outcome.value))
            try:
                r.outcome = ResolvedOutcome(outcome_str)
            except ValueError:
                pass  # keep deterministic outcome
            if "final_escalation_rung" in d:
                rung = int(d["final_escalation_rung"])
                r.final_escalation_rung = max(0, min(5, rung))
            if "arbiter_note" in d:
                r.arbiter_note = str(d["arbiter_note"])[:500]
            if "sequence_index" in d:
                r.sequence_index = max(0, int(d["sequence_index"]))
            if "trust_delta" in d:
                r.trust_delta = max(-100, min(100, int(d["trust_delta"])))
            if "hostility_delta" in d:
                r.hostility_delta = max(-100, min(100, int(d["hostility_delta"])))

        return pre_resolved
