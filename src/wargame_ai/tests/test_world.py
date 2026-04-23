"""Unit tests for :mod:`ai.sim.world`."""

from __future__ import annotations

import uuid

from wargame_shared.schemas.sim_event import Domain, SimEvent

from wargame_ai.sim.world import (
    CountryState,
    ProposedAction,
    RedLine,
    RedLineStatus,
    Relationship,
    ResolvedAction,
    ResolvedOutcome,
    WorldState,
)


def _make_world(codes: list[str] = ["CHN", "TWN", "USA"]) -> WorldState:
    world = WorldState()
    for code in codes:
        world.countries[code] = CountryState(
            iso3=code,
            name=code,
            red_lines=[RedLine(description=f"{code} red-line")],
        )
    # pre-create relationships
    for i, a in enumerate(codes):
        for b in codes[i + 1 :]:
            world.get_relationship(a, b)
    return world


class TestRelationshipOrdering:
    def test_pair_is_order_independent(self) -> None:
        world = _make_world()
        r1 = world.get_relationship("CHN", "TWN")
        r2 = world.get_relationship("TWN", "CHN")
        assert r1 is r2
        assert isinstance(r1, Relationship)

    def test_apply_delta_clamps(self) -> None:
        rel = Relationship()
        rel.apply_delta(trust_delta=200, hostility_delta=-50)
        assert rel.trust_score == 100
        assert rel.hostility_index == 0


class TestApply:
    def test_accepted_action_updates_relationship(self) -> None:
        world = _make_world()
        action = ResolvedAction(
            proposed=ProposedAction(
                actor="CHN",
                target="TWN",
                domain=Domain.economic,
                action_type="sanction",
                payload={"instrument": "sanction", "magnitude": "severe"},
                estimated_escalation_rung=3,
            ),
            outcome=ResolvedOutcome.accepted,
            final_escalation_rung=3,
            trust_delta=-15,
            hostility_delta=12,
        )
        world.apply(action)
        rel = world.get_relationship("CHN", "TWN")
        assert rel.trust_score == -15
        assert rel.hostility_index == 12
        assert "sanction" in rel.active_sanctions
        assert world.countries["CHN"].last_action is not None
        # Budget consumed
        assert world.countries["CHN"].resource_budget.economic < 100

    def test_rejected_action_is_noop(self) -> None:
        world = _make_world()
        action = ResolvedAction(
            proposed=ProposedAction(
                actor="CHN",
                target="TWN",
                domain=Domain.diplomatic,
                action_type="x",
            ),
            outcome=ResolvedOutcome.rejected,
        )
        world.apply(action)
        assert world.get_relationship("CHN", "TWN").trust_score == 0
        assert world.countries["CHN"].last_action is None

    def test_red_line_escalates_on_high_rung(self) -> None:
        world = _make_world()
        action = ResolvedAction(
            proposed=ProposedAction(
                actor="CHN",
                target="TWN",
                domain=Domain.kinetic_limited,
                action_type="strike",
            ),
            outcome=ResolvedOutcome.accepted,
            final_escalation_rung=3,
        )
        world.apply(action)
        assert world.countries["TWN"].red_lines[0].status is RedLineStatus.approached

        # Another high-rung action should cross the line
        action2 = ResolvedAction(
            proposed=ProposedAction(
                actor="CHN",
                target="TWN",
                domain=Domain.kinetic_limited,
                action_type="strike2",
            ),
            outcome=ResolvedOutcome.accepted,
            final_escalation_rung=4,
        )
        world.apply(action2)
        assert world.countries["TWN"].red_lines[0].status is RedLineStatus.crossed

    def test_unknown_actor_ignored(self) -> None:
        world = _make_world()
        action = ResolvedAction(
            proposed=ProposedAction(
                actor="ZZZ",
                target="TWN",
                domain=Domain.diplomatic,
                action_type="x",
            ),
            outcome=ResolvedOutcome.accepted,
        )
        world.apply(action)  # should not raise


class TestRecentEvents:
    def test_bounded_window(self) -> None:
        world = _make_world()
        world.max_recent_events = 3
        sim_id = uuid.uuid4()
        for i in range(10):
            world.record_event(
                SimEvent(
                    sim_id=sim_id,
                    turn=i,
                    actor_country="CHN",
                    target_country="TWN",
                    domain=Domain.info,
                    action_type=f"a{i}",
                )
            )
        assert len(world.recent_events) == 3
        # Most recent events preserved
        assert world.recent_events[-1].action_type == "a9"


class TestSnapshotAndSummarize:
    def test_snapshot_keys_are_string_pairs(self) -> None:
        world = _make_world()
        snap = world.snapshot()
        assert "CHN-TWN" in snap["relationships"]
        assert isinstance(snap["countries"]["CHN"], dict)

    def test_summarize_for_is_redacted(self) -> None:
        world = _make_world()
        sim_id = uuid.uuid4()
        world.record_event(
            SimEvent(
                sim_id=sim_id,
                turn=0,
                actor_country="CHN",
                target_country="TWN",
                domain=Domain.info,
                action_type="leak",
            )
        )
        world.record_event(
            SimEvent(
                sim_id=sim_id,
                turn=0,
                actor_country="USA",
                target_country=None,
                domain=Domain.diplomatic,
                action_type="statement",
            )
        )

        summary = world.summarize_for("CHN")
        assert summary["self"] is not None
        assert summary["self"]["iso3"] == "CHN"
        assert "CHN" not in summary["others"]
        # CHN was actor in one event, not in the second — they shouldn't see the USA→None event
        visible_actions = [e["action_type"] for e in summary["recent_events_involving_me"]]
        assert "leak" in visible_actions
        assert "statement" not in visible_actions
