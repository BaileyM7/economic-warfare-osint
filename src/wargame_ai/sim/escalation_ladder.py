"""Escalation-ladder classification.

Reuses the ``EscalationRung`` enum from ``shared.schemas.sim_event`` as the
canonical source of truth.  The ``classify_action()`` function maps a
``ProposedAction`` to a rung, using a naive domain → rung baseline today and
inviting the user to improve it.
"""

from __future__ import annotations

from wargame_shared.schemas.sim_event import Domain, EscalationRung

from wargame_ai.sim.world import ProposedAction

__all__ = ["EscalationRung", "classify_action"]


# Baseline domain → rung mapping.  The user should refine this with
# magnitude modifiers, target sensitivity, and cumulative crisis effects.
_DOMAIN_BASE_RUNG: dict[Domain, EscalationRung] = {
    Domain.info: EscalationRung.gray_zone,
    Domain.diplomatic: EscalationRung.peacetime,
    Domain.economic: EscalationRung.coercive_diplomacy,
    Domain.cyber: EscalationRung.gray_zone,
    Domain.kinetic_limited: EscalationRung.limited_conflict,
    Domain.kinetic_general: EscalationRung.general_war,
}


def classify_action(action: ProposedAction) -> EscalationRung:
    """Classify an action into an EscalationRung.

    # TODO(user): This is a learning-mode contribution point. The default below is a naive
    # domain→rung mapping. Replace with nuanced logic: magnitude modifiers, target sensitivity,
    # cumulative effect from world.active_crises, etc. ~10 lines.
    #
    # Ideas to consider:
    #   - Inspect ``action.payload.get("magnitude")`` for sanctions / strikes.
    #   - Distinguish ``destructive`` vs ``espionage`` cyber intent.
    #   - Bump the rung if the target has an active crossed red-line.
    #   - Bump the rung if the same actor has escalated in the last N turns.

    Args:
        action: The ProposedAction to classify.

    Returns:
        An EscalationRung enum value (0..5).
    """
    base = _DOMAIN_BASE_RUNG.get(action.domain, EscalationRung.peacetime)

    # Minimal "magnitude" heuristic so the baseline isn't literally flat.
    if action.domain is Domain.kinetic_limited:
        posture = str(action.payload.get("posture", "")).lower()
        if posture == "show_of_force":
            base = EscalationRung.coercive_diplomacy
        elif posture == "major_strike":
            base = EscalationRung.regional_war
    elif action.domain is Domain.cyber:
        intent = str(action.payload.get("intent", "")).lower()
        if intent == "destructive":
            base = EscalationRung.limited_conflict
    elif action.domain is Domain.economic:
        magnitude = str(action.payload.get("magnitude", "")).lower()
        if magnitude in ("severe", "total_embargo"):
            base = EscalationRung.limited_conflict

    # Clamp to the agent's own self-estimate as a lower bound when it is higher;
    # this lets the agent's judgment trump a generic domain mapping.
    rung_value = max(int(base), int(action.estimated_escalation_rung))
    rung_value = min(5, max(0, rung_value))
    return EscalationRung(rung_value)
