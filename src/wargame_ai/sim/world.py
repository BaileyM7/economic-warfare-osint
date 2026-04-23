"""World state — the shared, mutable simulation state.

`WorldState` is the single source of truth for a running simulation.  The
LangGraph graph threads this object through the perceive → decide → act →
arbitrate → apply → emit pipeline.

Design invariants:
  * Relationships are keyed by an *ordered* ``(actor, target)`` tuple.  The
    convention is the actor's ISO-3 code sorts lexicographically first; a
    helper ``_pair()`` normalises arbitrary input.
  * ``recent_events`` is a bounded deque-like window (max 20).  Older events
    fall out the tail so prompt budgets remain predictable.
  * ``summarize_for()`` returns a *redacted* view: each agent only sees what
    it plausibly knows (its own state, trusted allies' postures, and events
    in which it was an actor or target).
"""

from __future__ import annotations

import enum
from collections import deque
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from wargame_ai.agents.leader_profile import LeaderProfile
from wargame_shared.schemas.sim_event import Domain, Explainability, SimEvent


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class RedLineStatus(str, enum.Enum):
    """Lifecycle of a country's declared red-line condition."""

    inactive = "inactive"      # Not triggered; baseline state
    approached = "approached"  # World events suggest the red-line is near
    crossed = "crossed"        # Condition met; country is obligated to respond


class Posture(str, enum.Enum):
    """Coarse posture label per domain; LLM consumes this as prompt context."""

    cooperative = "cooperative"
    neutral = "neutral"
    defensive = "defensive"
    deterrent = "deterrent"
    aggressive = "aggressive"
    belligerent = "belligerent"


# ---------------------------------------------------------------------------
# Sub-models
# ---------------------------------------------------------------------------


class RedLine(BaseModel):
    """A specific condition that, if crossed, triggers a country's response."""

    model_config = ConfigDict(str_strip_whitespace=True)

    description: str = Field(..., min_length=1)
    status: RedLineStatus = RedLineStatus.inactive
    severity: int = Field(default=3, ge=1, le=5, description="How hard the country responds.")


class ResourceBudget(BaseModel):
    """Depletable capacity counters per domain.

    Each action consumes some budget; low budgets realistically constrain
    what the agent can propose on later turns.  Values are abstract 0..100.
    """

    model_config = ConfigDict(str_strip_whitespace=True)

    diplomatic: int = Field(default=100, ge=0, le=100)
    economic: int = Field(default=100, ge=0, le=100)
    military: int = Field(default=100, ge=0, le=100)
    cyber: int = Field(default=100, ge=0, le=100)
    information: int = Field(default=100, ge=0, le=100)

    def consume(self, domain: str, amount: int) -> None:
        """Deplete a given domain's budget (clamps at 0)."""
        attr = domain if hasattr(self, domain) else "diplomatic"
        current = getattr(self, attr)
        setattr(self, attr, max(0, current - amount))


class CountryState(BaseModel):
    """Per-country live state inside a running simulation."""

    model_config = ConfigDict(str_strip_whitespace=True)

    iso3: str = Field(..., min_length=3, max_length=3)
    name: str
    posture: dict[str, Posture] = Field(
        default_factory=lambda: {
            "diplomatic": Posture.neutral,
            "economic": Posture.neutral,
            "military": Posture.neutral,
            "cyber": Posture.neutral,
            "information": Posture.neutral,
        },
        description="Per-domain posture label.",
    )
    red_lines: list[RedLine] = Field(default_factory=list)
    last_action: dict[str, Any] | None = Field(
        default=None,
        description="Last ResolvedAction dict emitted by this country.",
    )
    resource_budget: ResourceBudget = Field(default_factory=ResourceBudget)
    doctrine: str = Field(default="", description="Free-form doctrine text fed to the prompt.")
    persona: str = Field(
        default="",
        description=(
            "Markdown persona (leadership, decision style, risk tolerance, "
            "escalation preferences). Stable across all turns of a sim; "
            "injected into the agent prompt to shape reasoning voice."
        ),
    )
    leader_profile: LeaderProfile | None = Field(
        default=None,
        description=(
            "Structured Big-Five (OCEAN) profile parsed from the persona file's "
            "YAML frontmatter. None when the persona file omits frontmatter; "
            "the prompt then renders a placeholder line."
        ),
    )
    consecutive_no_action_turns: int = Field(
        default=0,
        ge=0,
        description=(
            "Number of consecutive accepted turns in which this country chose "
            "(or fell back to) no_action. Incremented in WorldState.apply when "
            "action_type == 'no_action', reset to 0 on any other action. Used "
            "by render_country_prompt to inject a streak-pressure line when "
            "the count crosses a threshold — keeps agents from drifting into "
            "permanent inaction without justification."
        ),
    )
    recent_domains: list[str] = Field(
        default_factory=list,
        description=(
            "Rolling window of the last N non-no_action domains this country "
            "used, most-recent-last. Maxlen is enforced in apply(); the "
            "prompt reads this to nudge the agent toward cross-domain "
            "variety when the same domain keeps repeating. Demo-centric: "
            "real crises do cluster within one domain, but showing variety "
            "makes for a more informative visualization."
        ),
    )


class Relationship(BaseModel):
    """Bilateral relationship between two countries.

    Keyed in ``WorldState.relationships`` by an ordered (iso3_a, iso3_b) tuple
    where iso3_a < iso3_b lexicographically.
    """

    model_config = ConfigDict(str_strip_whitespace=True)

    trust_score: int = Field(default=0, ge=-100, le=100)
    hostility_index: int = Field(default=0, ge=0, le=100)
    active_agreements: list[str] = Field(default_factory=list)
    active_sanctions: list[str] = Field(default_factory=list)

    def apply_delta(self, trust_delta: int = 0, hostility_delta: int = 0) -> None:
        """Clamp-adjusted in-place modification of the relationship."""
        self.trust_score = max(-100, min(100, self.trust_score + trust_delta))
        self.hostility_index = max(0, min(100, self.hostility_index + hostility_delta))


class CrisisDescriptor(BaseModel):
    """A named, in-progress crisis that colours agent perception."""

    model_config = ConfigDict(str_strip_whitespace=True)

    name: str
    involved: list[str] = Field(default_factory=list, description="ISO-3 codes.")
    started_turn: int = Field(..., ge=0)
    intensity: int = Field(default=3, ge=1, le=5)
    summary: str = ""


# ---------------------------------------------------------------------------
# Action DTOs (shared by agents, arbiter, and world_updater)
# ---------------------------------------------------------------------------


class ProposedAction(BaseModel):
    """An action proposed by a single country agent in a turn.

    The arbiter consumes these, deduplicates, sequences, and either resolves
    or rejects each one.
    """

    model_config = ConfigDict(str_strip_whitespace=True)

    actor: str = Field(..., min_length=3, max_length=3)
    target: str | None = Field(default=None, min_length=3, max_length=3)
    domain: Domain
    action_type: str = Field(..., min_length=1, max_length=128)
    payload: dict[str, Any] = Field(default_factory=dict)
    rationale: str = ""
    estimated_escalation_rung: int = Field(default=0, ge=0, le=5)
    explainability: Explainability | None = Field(
        default=None,
        description=(
            "Structured triplet (summary / triggering_factors / intended_outcome) "
            "extracted from the agent's tool-call. None for fallback no_action "
            "paths or seed events; populated for every real agent decision."
        ),
    )


class ResolvedOutcome(str, enum.Enum):
    """How the arbiter disposed of a proposed action."""

    accepted = "accepted"
    merged = "merged"      # Deduplicated into a sibling action
    rejected = "rejected"  # Logically impossible / invalid


class ResolvedAction(BaseModel):
    """Arbiter-resolved action ready to mutate world state and emit a SimEvent."""

    model_config = ConfigDict(str_strip_whitespace=True)

    proposed: ProposedAction
    outcome: ResolvedOutcome = ResolvedOutcome.accepted
    final_escalation_rung: int = Field(default=0, ge=0, le=5)
    arbiter_note: str = ""
    sequence_index: int = Field(default=0, ge=0, description="Temporal order within the turn.")
    trust_delta: int = Field(default=0, ge=-100, le=100)
    hostility_delta: int = Field(default=0, ge=-100, le=100)


# ---------------------------------------------------------------------------
# WorldState
# ---------------------------------------------------------------------------


def _pair(a: str, b: str) -> tuple[str, str]:
    """Return a lexicographically ordered pair of ISO-3 codes."""
    return (a, b) if a <= b else (b, a)


class WorldState(BaseModel):
    """The complete mutable state of a simulation at a given turn."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    turn: int = Field(default=0, ge=0)
    countries: dict[str, CountryState] = Field(default_factory=dict)
    # Pydantic cannot use tuple keys in JSON mode, but Python dicts keyed by
    # tuples are fine for in-memory state.  Snapshot flattens them to strings.
    relationships: dict[tuple[str, str], Relationship] = Field(default_factory=dict)
    recent_events: list[SimEvent] = Field(default_factory=list)
    active_crises: list[CrisisDescriptor] = Field(default_factory=list)
    max_recent_events: int = Field(default=20, ge=1, le=200)

    # ------------------------------------------------------------------ #
    # Relationship accessors                                               #
    # ------------------------------------------------------------------ #

    def get_relationship(self, a: str, b: str) -> Relationship:
        """Return (or lazily create) the relationship between two countries."""
        key = _pair(a, b)
        if key not in self.relationships:
            self.relationships[key] = Relationship()
        return self.relationships[key]

    # ------------------------------------------------------------------ #
    # State mutation                                                       #
    # ------------------------------------------------------------------ #

    def apply(self, action: ResolvedAction) -> None:
        """Apply a ResolvedAction to the world state.

        Updates:
          * resource budget of the actor (domain-specific drain)
          * relationship trust / hostility deltas
          * last_action pointer on the actor's CountryState
          * sanctions / agreements lists for economic / diplomatic actions
          * red-line status of the target (if the action is severe enough)
        """
        if action.outcome is not ResolvedOutcome.accepted:
            return

        proposed = action.proposed
        actor_state = self.countries.get(proposed.actor)
        if actor_state is None:
            return  # Unknown actor — silently ignore

        # --- Budget consumption -----------------------------------------
        domain_to_budget = {
            Domain.diplomatic: "diplomatic",
            Domain.economic: "economic",
            Domain.cyber: "cyber",
            Domain.info: "information",
            Domain.kinetic_limited: "military",
            Domain.kinetic_general: "military",
        }
        cost = 5 + action.final_escalation_rung * 3
        actor_state.resource_budget.consume(
            domain_to_budget.get(proposed.domain, "diplomatic"), cost
        )

        # --- Relationship deltas ----------------------------------------
        if proposed.target and proposed.target in self.countries:
            rel = self.get_relationship(proposed.actor, proposed.target)
            rel.apply_delta(
                trust_delta=action.trust_delta,
                hostility_delta=action.hostility_delta,
            )

            # Track sanctions / agreements explicitly
            if proposed.domain is Domain.economic:
                instrument = proposed.payload.get("instrument", proposed.action_type)
                if "sanction" in proposed.action_type.lower() and instrument not in rel.active_sanctions:
                    rel.active_sanctions.append(str(instrument))
            if proposed.domain is Domain.diplomatic and "agreement" in proposed.action_type.lower():
                name = str(proposed.payload.get("agreement", proposed.action_type))
                if name not in rel.active_agreements:
                    rel.active_agreements.append(name)

        # --- Last action pointer ----------------------------------------
        actor_state.last_action = {
            "turn": self.turn,
            "domain": proposed.domain.value,
            "action_type": proposed.action_type,
            "target": proposed.target,
            "rung": action.final_escalation_rung,
        }

        # --- Inactivity streak tracker ----------------------------------
        # Incremented on no_action (including LLM-failure fallbacks), reset
        # on any substantive action. Consumed by render_country_prompt to
        # inject a pressure line at the threshold.
        if proposed.action_type == "no_action":
            actor_state.consecutive_no_action_turns += 1
        else:
            actor_state.consecutive_no_action_turns = 0
            # Record the domain used for variety tracking.  no_action calls
            # are excluded — they're handled by the separate inactivity
            # streak.  We cap the window at 3; the prompt only needs to
            # know "last 2 were the same" to nudge.
            window = list(actor_state.recent_domains)
            window.append(proposed.domain.value)
            actor_state.recent_domains = window[-3:]

        # --- Red-line escalation ----------------------------------------
        if proposed.target and proposed.target in self.countries and action.final_escalation_rung >= 3:
            target_state = self.countries[proposed.target]
            for rl in target_state.red_lines:
                if rl.status is RedLineStatus.inactive:
                    rl.status = RedLineStatus.approached
                elif rl.status is RedLineStatus.approached and action.final_escalation_rung >= 4:
                    rl.status = RedLineStatus.crossed

    def record_event(self, event: SimEvent) -> None:
        """Append a SimEvent to the bounded recent_events window."""
        window: deque[SimEvent] = deque(self.recent_events, maxlen=self.max_recent_events)
        window.append(event)
        self.recent_events = list(window)

    # ------------------------------------------------------------------ #
    # Serialisation                                                        #
    # ------------------------------------------------------------------ #

    def snapshot(self) -> dict[str, Any]:
        """Return a JSON-safe snapshot of the full world state.

        Tuple-keyed relationships are flattened to ``'ISO3A-ISO3B'`` strings.
        """
        return {
            "turn": self.turn,
            "countries": {
                code: c.model_dump(mode="json") for code, c in self.countries.items()
            },
            "relationships": {
                f"{a}-{b}": rel.model_dump(mode="json")
                for (a, b), rel in self.relationships.items()
            },
            "recent_events": [e.model_dump(mode="json") for e in self.recent_events],
            "active_crises": [c.model_dump(mode="json") for c in self.active_crises],
        }

    def summarize_for(self, country_code: str) -> dict[str, Any]:
        """Return a redacted view of the world from a single country's POV.

        An agent sees:
          * Its own full CountryState.
          * Posture labels (not resource budgets) of all other countries.
          * Relationships in which it is a participant.
          * Events in which it was actor or target in the recent window.
          * All active crises (public knowledge).
        """
        self_state = self.countries.get(country_code)
        others = {
            code: {
                "name": c.name,
                "posture": {k: v.value for k, v in c.posture.items()},
                "last_action": c.last_action,
            }
            for code, c in self.countries.items()
            if code != country_code
        }
        my_rels: dict[str, dict[str, Any]] = {}
        for (a, b), rel in self.relationships.items():
            if country_code in (a, b):
                other = b if a == country_code else a
                my_rels[other] = rel.model_dump(mode="json")

        visible_events = [
            e.model_dump(mode="json")
            for e in self.recent_events
            if e.actor_country == country_code or e.target_country == country_code
        ]

        return {
            "turn": self.turn,
            "self": self_state.model_dump(mode="json") if self_state else None,
            "others": others,
            "relationships": my_rels,
            "recent_events_involving_me": visible_events,
            "active_crises": [c.model_dump(mode="json") for c in self.active_crises],
        }
