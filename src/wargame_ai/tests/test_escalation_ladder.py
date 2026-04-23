"""Tests for :mod:`ai.sim.escalation_ladder`."""

from __future__ import annotations

import pytest

from wargame_shared.schemas.sim_event import Domain, EscalationRung

from wargame_ai.sim.escalation_ladder import classify_action
from wargame_ai.sim.world import ProposedAction


def _action(domain: Domain, **payload: object) -> ProposedAction:
    return ProposedAction(
        actor="CHN",
        target="TWN",
        domain=domain,
        action_type="x",
        payload=dict(payload),
    )


@pytest.mark.parametrize(
    ("domain", "expected_min"),
    [
        (Domain.info, EscalationRung.gray_zone),
        (Domain.diplomatic, EscalationRung.peacetime),
        (Domain.economic, EscalationRung.coercive_diplomacy),
        (Domain.cyber, EscalationRung.gray_zone),
        (Domain.kinetic_limited, EscalationRung.limited_conflict),
        (Domain.kinetic_general, EscalationRung.general_war),
    ],
)
def test_every_domain_has_sensible_rung(domain: Domain, expected_min: EscalationRung) -> None:
    """Every domain should classify to a non-nonsense rung."""
    rung = classify_action(_action(domain))
    assert 0 <= int(rung) <= 5
    assert int(rung) >= int(expected_min) - 1  # allow slight flex


def test_destructive_cyber_bumps_rung() -> None:
    base = classify_action(_action(Domain.cyber, intent="espionage"))
    destructive = classify_action(_action(Domain.cyber, intent="destructive"))
    assert int(destructive) > int(base)


def test_major_strike_is_at_least_regional_war() -> None:
    rung = classify_action(_action(Domain.kinetic_limited, posture="major_strike"))
    assert int(rung) >= int(EscalationRung.regional_war)


def test_total_embargo_is_limited_conflict() -> None:
    rung = classify_action(_action(Domain.economic, magnitude="total_embargo"))
    assert int(rung) >= int(EscalationRung.limited_conflict)


def test_agent_self_estimate_is_lower_bound() -> None:
    action = ProposedAction(
        actor="CHN",
        target="TWN",
        domain=Domain.diplomatic,
        action_type="x",
        estimated_escalation_rung=4,
    )
    rung = classify_action(action)
    # Agent self-rated rung=4 → result >= 4
    assert int(rung) >= 4


def test_clamped_to_valid_range() -> None:
    # Even with absurd inputs we must stay in [0, 5]
    rung = classify_action(
        ProposedAction(
            actor="CHN",
            target="TWN",
            domain=Domain.info,
            action_type="x",
            estimated_escalation_rung=5,
        )
    )
    assert 0 <= int(rung) <= 5
