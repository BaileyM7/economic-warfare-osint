"""Country-agent LangGraph node.

A ``CountryAgent`` represents a single national decision-maker.  It is invoked
once per turn with a ``Perception`` (redacted world view + retrieved memories)
and returns a ``ProposedAction`` via Anthropic tool use.

Design notes:
  * The LLM is driven with **tool use**, not JSON mode.  Tool schemas are the
    enum of legal action types; forcing the model through a tool guarantees
    structured output and provides built-in validation.
  * Each tool returns a ``ProposedAction`` — we do not accept free-form text.
  * The system prompt template is loaded from ``prompts/country_agent.md`` and
    rendered with ``str.format`` (simple, readable, auditable).
  * Tests inject a fake Anthropic client (any object with ``.ainvoke(messages)``
    → returning a message whose ``tool_calls`` attribute is set).  The real
    implementation uses ``langchain_anthropic.ChatAnthropic``.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

import structlog
from pydantic import BaseModel, ConfigDict, Field

from wargame_ai.agents.leader_profile import LeaderProfile, render_leader_profile_block
from wargame_ai.sim.signals import Signal, render_signals_block
from wargame_shared.schemas.sim_event import (
    Domain,
    Explainability,
    FactorKind,
    TriggeringFactor,
)

from wargame_ai.sim.world import ProposedAction

log = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Tool schemas (Anthropic tool-use JSON format)
# ---------------------------------------------------------------------------


_COMMON_SCHEMA_BASE: dict[str, Any] = {
    "rationale": {
        "type": "string",
        "description": "2–4 sentence reasoning chain; audit-quality prose.",
    },
    "estimated_escalation_rung": {
        "type": "integer",
        "minimum": 0,
        "maximum": 5,
        "description": "0=peacetime, 1=gray_zone, 2=coercive, 3=limited, 4=regional, 5=general.",
    },
    # Structured explainability: required on every action so the UI can render
    # "X did Y because Z in hopes of W" without parsing free prose.
    "summary": {
        "type": "string",
        "maxLength": 160,
        "description": (
            "One verb-phrase line naming what you did. "
            "e.g. 'Imposed targeted sanctions on TSMC exports.' Max 160 chars."
        ),
    },
    "triggering_factors": {
        "type": "array",
        "minItems": 1,
        "maxItems": 4,
        "description": (
            "1–4 evidentiary factors that drove this choice. Each must point to "
            "something concrete in your perception — do not invent factors."
        ),
        "items": {
            "type": "object",
            "properties": {
                "kind": {
                    "type": "string",
                    "enum": ["event", "red_line", "memory", "posture", "perception"],
                    "description": (
                        "Class of evidence: 'event' for a SimEvent UUID from "
                        "recent_events_involving_me; 'red_line' for one of your "
                        "declared red-line slugs; 'memory' for a recalled memory "
                        "(ref='turn:N'); 'posture' for an ISO3-ISO3 pair you "
                        "observed; 'perception' for a perception field path."
                    ),
                },
                "ref": {
                    "type": "string",
                    "description": (
                        "Reference whose meaning depends on `kind`. Event UUID, "
                        "red-line slug or first 6 words, 'turn:N', 'USA-TWN', etc."
                    ),
                },
                "note": {
                    "type": "string",
                    "maxLength": 200,
                    "description": "One short clause explaining what about this factor drove the choice.",
                },
            },
            "required": ["kind", "ref", "note"],
        },
    },
    "intended_outcome": {
        "type": "string",
        "maxLength": 240,
        "description": "One sentence stating the result you hope this action causes.",
    },
}


# Required fields the explainability triplet adds to every tool's `required` list.
_EXPLAINABILITY_REQUIRED: list[str] = ["summary", "triggering_factors", "intended_outcome"]


COUNTRY_AGENT_TOOLS: list[dict[str, Any]] = [
    {
        "name": "diplomatic_action",
        "description": "Issue a diplomatic statement, recall an envoy, call a summit.",
        "input_schema": {
            "type": "object",
            "properties": {
                "target": {"type": "string", "description": "ISO-3 code of target country."},
                "action_type": {
                    "type": "string",
                    "description": "Slug, e.g. 'formal_protest', 'envoy_recall', 'summit_call'.",
                },
                "severity": {
                    "type": "string",
                    "enum": ["mild", "moderate", "severe"],
                },
                "message": {"type": "string", "description": "Public text of the statement."},
                **_COMMON_SCHEMA_BASE,
            },
            "required": [
                "target", "action_type", "severity", "message", "rationale",
                *_EXPLAINABILITY_REQUIRED,
            ],
        },
    },
    {
        "name": "economic_action",
        "description": "Impose sanctions, tariffs, export controls, or asset freezes.",
        "input_schema": {
            "type": "object",
            "properties": {
                "target": {"type": "string"},
                "instrument": {
                    "type": "string",
                    "enum": ["sanction", "tariff", "export_control", "asset_freeze", "embargo"],
                },
                "magnitude": {
                    "type": "string",
                    "enum": ["symbolic", "targeted", "broad", "severe", "total_embargo"],
                },
                **_COMMON_SCHEMA_BASE,
            },
            "required": [
                "target", "instrument", "magnitude", "rationale",
                *_EXPLAINABILITY_REQUIRED,
            ],
        },
    },
    {
        "name": "information_action",
        "description": "Run a propaganda operation, leak intelligence, or issue a public statement.",
        "input_schema": {
            "type": "object",
            "properties": {
                "channel": {
                    "type": "string",
                    "enum": ["state_media", "social_media", "covert_leak", "press_conference"],
                },
                "target": {
                    "type": "string",
                    "description": "ISO-3 of targeted country; empty string for domestic/global.",
                },
                "content_type": {
                    "type": "string",
                    "enum": ["propaganda", "leak", "public_statement", "denial"],
                },
                **_COMMON_SCHEMA_BASE,
            },
            "required": [
                "channel", "target", "content_type", "rationale",
                *_EXPLAINABILITY_REQUIRED,
            ],
        },
    },
    {
        "name": "cyber_action",
        "description": "Execute a cyber operation against a target.",
        "input_schema": {
            "type": "object",
            "properties": {
                "target": {"type": "string"},
                "vector": {
                    "type": "string",
                    "enum": ["phishing", "supply_chain", "network_intrusion", "wiper", "ddos"],
                },
                "intent": {
                    "type": "string",
                    "enum": ["espionage", "disruption", "destructive"],
                },
                **_COMMON_SCHEMA_BASE,
            },
            "required": [
                "target", "vector", "intent", "rationale",
                *_EXPLAINABILITY_REQUIRED,
            ],
        },
    },
    {
        "name": "kinetic_action",
        "description": "Deploy military force: show-of-force, limited strike, or major strike.",
        "input_schema": {
            "type": "object",
            "properties": {
                "target": {"type": "string"},
                "asset": {"type": "string", "description": "Asset deployed (e.g. 'carrier_group')."},
                "posture": {
                    "type": "string",
                    "enum": ["show_of_force", "limited_strike", "major_strike"],
                },
                **_COMMON_SCHEMA_BASE,
            },
            "required": [
                "target", "asset", "posture", "rationale",
                *_EXPLAINABILITY_REQUIRED,
            ],
        },
    },
    {
        "name": "no_action",
        "description": "Explicit inaction. A legitimate strategic choice.",
        "input_schema": {
            "type": "object",
            "properties": {
                "reason": {"type": "string"},
                **_COMMON_SCHEMA_BASE,
            },
            "required": [
                "reason", "rationale",
                *_EXPLAINABILITY_REQUIRED,
            ],
        },
    },
]


# Map tool name → Domain
_TOOL_DOMAIN: dict[str, Domain] = {
    "diplomatic_action": Domain.diplomatic,
    "economic_action": Domain.economic,
    "information_action": Domain.info,
    "cyber_action": Domain.cyber,
    "kinetic_action": Domain.kinetic_limited,  # kinetic_general only for major_strike
    "no_action": Domain.diplomatic,  # sentinel; no_action never emits a SimEvent
}


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


class MemoryRecord(BaseModel):
    """Single retrieved memory fragment (subset of the stored row)."""

    model_config = ConfigDict(str_strip_whitespace=True)

    content: str
    memory_type: str = "observation"
    turn: int = 0
    score: float = 0.0


@dataclass
class Perception:
    """Bundle of everything the agent sees in the `perceive` step."""

    country_iso3: str
    country_name: str
    doctrine: str
    red_lines: list[str]
    current_posture: dict[str, str]
    resource_budget: dict[str, int]
    world_view: dict[str, Any]  # from WorldState.summarize_for()
    memories: list[MemoryRecord] = field(default_factory=list)
    persona: str = ""
    leader_profile: LeaderProfile | None = None
    recent_signals: list[Signal] = field(default_factory=list)
    consecutive_no_action_turns: int = 0
    recent_domains: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# LLM client protocol (for testability)
# ---------------------------------------------------------------------------


class ToolCall(BaseModel):
    """Shape of a tool-use response chunk — matches LangChain's structure."""

    model_config = ConfigDict(extra="allow")

    name: str
    args: dict[str, Any]
    id: str = ""


class LLMResponse(BaseModel):
    """Minimal subset of ``AIMessage`` that the agent needs."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    tool_calls: list[ToolCall] = Field(default_factory=list)
    content: str = ""


class LLMClient(Protocol):
    """Anything that can accept (system, human) messages and return tool calls."""

    async def ainvoke_tools(
        self,
        system_prompt: str,
        human_prompt: str,
        tools: list[dict[str, Any]],
    ) -> LLMResponse:  # pragma: no cover — protocol only
        ...


# ---------------------------------------------------------------------------
# Prompt rendering
# ---------------------------------------------------------------------------


_PROMPT_PATH = Path(__file__).parent / "prompts" / "country_agent.md"

# Nudges — defaults chosen to be noticeable but not pushy.  Tunable via env
# so a running sim can be tightened without a code change.
_NO_ACTION_STREAK_THRESHOLD = int(os.environ.get("NO_ACTION_STREAK_THRESHOLD", "2"))
_HIGH_SIGNAL_MAGNITUDE_THRESHOLD = float(
    os.environ.get("HIGH_SIGNAL_MAGNITUDE_THRESHOLD", "0.7")
)
# Demo-oriented: if a country has repeated the same domain this many turns
# in a row, the prompt nudges it to consider a cross-domain move.  Lowered
# below 2 would force unnatural churn; higher than 3 rarely fires.
_DOMAIN_REPEAT_THRESHOLD = int(os.environ.get("DOMAIN_REPEAT_THRESHOLD", "2"))

# ---------------------------------------------------------------------------
# Prompt-caching boundary
# ---------------------------------------------------------------------------
#
# Anthropic prompt caching is billed at 0.1× for cache reads, 1.25× for
# writes, with a 5-minute TTL.  To get the benefit, the request must carry
# a ``cache_control`` marker at the end of the STABLE prefix of the system
# prompt.  Everything before the marker is cached; everything after is
# re-billed at normal rates every turn.
#
# The country-agent template is laid out so the cut is natural:
#   * Stable (cached): doctrine + red lines + leader OCEAN profile +
#     persona + universal rules — all identical turn-to-turn for a given
#     country.  ~2.2k tokens typically.
#   * Volatile (not cached): recent intelligence + current posture +
#     resource budget + memory + redacted world view — changes every turn.
#     ~1.8k tokens typically.
#
# The boundary is the literal "## Recent intelligence …" header.  If the
# template ever moves that header, update this constant.  When the marker
# is missing (older template / custom renderer), ``_split_for_cache``
# returns the whole text as the stable prefix with an empty suffix — a
# safe degradation: caching still works, just less efficiently.
_PROMPT_CACHE_BOUNDARY = "## Recent intelligence (last 24h, top signals)"


def _split_for_cache(rendered: str) -> tuple[str, str]:
    """Split a rendered system prompt into (cacheable_prefix, volatile_suffix).

    The split happens at the first occurrence of ``_PROMPT_CACHE_BOUNDARY``;
    the boundary line itself lives in the volatile suffix so the intel
    header always renders fresh at the top of the per-turn content.
    """
    idx = rendered.find(_PROMPT_CACHE_BOUNDARY)
    if idx < 0:
        return rendered, ""
    return rendered[:idx].rstrip() + "\n", rendered[idx:]


def _load_prompt_template() -> str:
    """Read the country-agent system-prompt template from disk."""
    return _PROMPT_PATH.read_text(encoding="utf-8")


def _streak_pressure_line(streak: int) -> str:
    """Render a one-line streak warning when the threshold is crossed.

    Respects agent agency — doesn't forbid no_action, just flags the
    cumulative reputational cost so the LLM knows it's a non-trivial
    choice this turn.  Returns an empty string below the threshold.
    """
    if streak < _NO_ACTION_STREAK_THRESHOLD:
        return ""
    return (
        f"\n\n⚠ **Inactivity streak: {streak} consecutive turns of no_action.**\n"
        "Your peers are updating their read of you toward *disengaged*.\n"
        "Inaction remains valid but requires explicit justification this turn —\n"
        "either articulate why continued restraint serves your doctrine, or act."
    )


def _domain_variety_pressure_line(recent_domains: list[str]) -> str:
    """Render a cross-domain nudge when the country has repeated a domain.

    Fires when the last ``_DOMAIN_REPEAT_THRESHOLD`` entries in
    ``recent_domains`` are all the same domain.  Respects agency — the
    prompt says "consider" not "must" — but raises the rhetorical cost of
    picking the same domain a third time.  Demo-oriented: real crises do
    cluster within a single domain, but the visualization reads better
    when action types vary across the globe arcs.
    """
    if len(recent_domains) < _DOMAIN_REPEAT_THRESHOLD:
        return ""
    tail = recent_domains[-_DOMAIN_REPEAT_THRESHOLD:]
    if len(set(tail)) != 1:
        return ""
    repeated = tail[0]
    # Available alternatives — pick any domain not equal to the repeated one.
    alternatives = [
        d for d in ("economic", "cyber", "info", "kinetic_limited", "diplomatic")
        if d != repeated
    ]
    alt_phrase = ", ".join(alternatives)
    return (
        f"\n\n⚠ **Domain-repetition: your last {_DOMAIN_REPEAT_THRESHOLD} "
        f"actions were all `{repeated}`.**\n"
        "Real crises rarely stay in one domain — adversaries read single-"
        "domain pounding as bluff.\n"
        f"Consider whether a move in a different domain ({alt_phrase}) "
        "would advance your strategy further than repeating.\n"
        "Repeating is still permitted if your doctrine truly requires it — "
        "but justify it explicitly."
    )


def _high_signal_pressure_line(signals: list["Signal"]) -> str:
    """Render a one-line high-magnitude pressure when any signal crosses
    the threshold.  Called from render_country_prompt.  Returns empty when
    no signal is above the threshold (which is the common case).
    """
    if not signals:
        return ""
    top_magnitude = max(s.magnitude for s in signals)
    if top_magnitude < _HIGH_SIGNAL_MAGNITUDE_THRESHOLD:
        return ""
    loud = max(signals, key=lambda s: s.magnitude)
    return (
        f"\n\n⚠ **High-magnitude signal this turn (mag {top_magnitude:.2f}): "
        f"{loud.source}.**\n"
        "Strong signals of this size usually warrant an explicit response.\n"
        "Ignoring this signal is permitted but must be reasoned — cite it in\n"
        "`triggering_factors` with a rationale for why it does NOT change\n"
        "your posture, or act on it."
    )


def render_country_prompt(perception: Perception, memories: list[MemoryRecord]) -> str:
    """Render the system prompt for a specific country's turn.

    The template uses ``{placeholder}`` tokens that we substitute via
    ``str.format_map`` so unknown placeholders raise instead of silently
    rendering empty.
    """
    template = _load_prompt_template()

    memory_block = (
        "\n".join(f"- [{m.memory_type} t={m.turn}] {m.content}" for m in memories)
        if memories
        else "(no prior memories)"
    )

    # We cannot use str.format_map naively because the prompt body contains
    # braces in HTML-comment examples.  Use a simple ``.replace()`` loop on
    # the handful of tokens we actually care about.
    subs = {
        "{country_name}": perception.country_name,
        "{country_iso3}": perception.country_iso3,
        "{turn}": str(perception.world_view.get("turn", 0)),
        "{doctrine}": perception.doctrine or "(no explicit doctrine set)",
        "{red_lines}": (
            "\n".join(f"- {r}" for r in perception.red_lines)
            if perception.red_lines
            else "(none declared)"
        ),
        "{leader_profile}": render_leader_profile_block(perception.leader_profile),
        # Append optional pressure lines to the signals block so the warning
        # reads as part of the intelligence section rather than a detached
        # scold. Both return "" below their thresholds — common case is no
        # change to the rendered block.
        "{recent_signals}": (
            render_signals_block(perception.recent_signals)
            + _streak_pressure_line(perception.consecutive_no_action_turns)
            + _high_signal_pressure_line(perception.recent_signals)
            + _domain_variety_pressure_line(perception.recent_domains)
        ),
        "{persona}": perception.persona or "(no persona authored for this country)",
        "{current_posture}": json.dumps(perception.current_posture, indent=2),
        "{resource_budget}": json.dumps(perception.resource_budget, indent=2),
        "{memory_snippets}": memory_block,
        "{recent_perception}": json.dumps(perception.world_view, indent=2, default=str),
    }
    rendered = template
    for token, value in subs.items():
        rendered = rendered.replace(token, value)
    return rendered


# ---------------------------------------------------------------------------
# Tool-call → ProposedAction mapping
# ---------------------------------------------------------------------------


def _extract_explainability(args: dict[str, Any]) -> Explainability | None:
    """Build an :class:`Explainability` from raw tool-call args, or None.

    Returns None (not a partial object) if any of the three required pieces is
    missing or unparseable — keeps invariant "if Explainability exists, it is
    fully populated and validated."
    """
    summary = str(args.get("summary", "")).strip()
    intended = str(args.get("intended_outcome", "")).strip()
    raw_factors = args.get("triggering_factors") or []
    if not summary or not intended or not isinstance(raw_factors, list) or not raw_factors:
        return None

    factors: list[TriggeringFactor] = []
    for raw in raw_factors:
        if not isinstance(raw, dict):
            continue
        kind_str = str(raw.get("kind", "")).strip().lower()
        ref = str(raw.get("ref", "")).strip()
        note = str(raw.get("note", "")).strip()
        if not kind_str or not ref or not note:
            continue
        try:
            kind = FactorKind(kind_str)
        except ValueError:
            log.debug("explainability_unknown_factor_kind", kind=kind_str)
            continue
        try:
            factors.append(TriggeringFactor(kind=kind, ref=ref, note=note))
        except Exception as exc:  # noqa: BLE001
            log.debug("explainability_factor_dropped", error=str(exc), ref=ref)
            continue

    if not factors:
        return None

    try:
        return Explainability(
            summary=summary[:160],
            triggering_factors=factors[:4],
            intended_outcome=intended[:240],
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("explainability_build_failed", error=str(exc))
        return None


def tool_call_to_action(actor: str, call: ToolCall) -> ProposedAction:
    """Convert an Anthropic tool-use call into a ``ProposedAction``.

    The ``actor`` field is injected (not part of the tool schema — the agent
    cannot spoof being a different country).

    Raises:
        ValueError: If the tool name is unknown.
    """
    name = call.name
    args = call.args
    rationale = str(args.get("rationale", ""))
    estimated = int(args.get("estimated_escalation_rung", 0))
    explainability = _extract_explainability(args)

    if name == "diplomatic_action":
        return ProposedAction(
            actor=actor,
            target=_normalise_target(args.get("target")),
            domain=Domain.diplomatic,
            action_type=str(args.get("action_type", "statement")),
            payload={
                "severity": args.get("severity"),
                "message": args.get("message", ""),
            },
            rationale=rationale,
            estimated_escalation_rung=estimated,
            explainability=explainability,
        )
    if name == "economic_action":
        return ProposedAction(
            actor=actor,
            target=_normalise_target(args.get("target")),
            domain=Domain.economic,
            action_type=str(args.get("instrument", "sanction")),
            payload={
                "instrument": args.get("instrument"),
                "magnitude": args.get("magnitude"),
            },
            rationale=rationale,
            estimated_escalation_rung=estimated,
            explainability=explainability,
        )
    if name == "information_action":
        return ProposedAction(
            actor=actor,
            target=_normalise_target(args.get("target")),
            domain=Domain.info,
            action_type=str(args.get("content_type", "public_statement")),
            payload={
                "channel": args.get("channel"),
                "content_type": args.get("content_type"),
            },
            rationale=rationale,
            estimated_escalation_rung=estimated,
            explainability=explainability,
        )
    if name == "cyber_action":
        return ProposedAction(
            actor=actor,
            target=_normalise_target(args.get("target")),
            domain=Domain.cyber,
            action_type=str(args.get("vector", "intrusion")),
            payload={
                "vector": args.get("vector"),
                "intent": args.get("intent"),
            },
            rationale=rationale,
            estimated_escalation_rung=estimated,
            explainability=explainability,
        )
    if name == "kinetic_action":
        posture = str(args.get("posture", "show_of_force"))
        # Map major_strike → kinetic_general
        domain = Domain.kinetic_general if posture == "major_strike" else Domain.kinetic_limited
        return ProposedAction(
            actor=actor,
            target=_normalise_target(args.get("target")),
            domain=domain,
            action_type=posture,
            payload={
                "asset": args.get("asset"),
                "posture": posture,
            },
            rationale=rationale,
            estimated_escalation_rung=estimated,
            explainability=explainability,
        )
    if name == "no_action":
        return ProposedAction(
            actor=actor,
            target=None,
            domain=Domain.diplomatic,
            action_type="no_action",
            payload={"reason": args.get("reason", "")},
            rationale=rationale,
            estimated_escalation_rung=0,
            explainability=explainability,
        )
    raise ValueError(f"Unknown tool call: {name}")


def _normalise_target(value: Any) -> str | None:
    """Accept str/None; strip whitespace; uppercase; return None for blanks."""
    if value is None:
        return None
    s = str(value).strip().upper()
    if not s or len(s) != 3:
        return None
    return s


# ---------------------------------------------------------------------------
# CountryAgent
# ---------------------------------------------------------------------------


class CountryAgent:
    """One LLM-backed agent representing a single country's decision process."""

    def __init__(
        self,
        code: str,
        name: str,
        doctrine: str,
        red_lines: list[str],
        llm: LLMClient,
        persona: str = "",
    ) -> None:
        """
        Args:
            code: ISO-3 code (uppercased).
            name: Display name.
            doctrine: Free-form doctrine text.
            red_lines: List of red-line description strings.
            llm: A client implementing :class:`LLMClient`.
            persona: Markdown persona (leadership, decision style, risk
                tolerance). Stable for the lifetime of the sim; injected
                into every turn's system prompt.
        """
        if len(code) != 3:
            raise ValueError(f"code must be ISO-3 (got {code!r})")
        self.code = code.upper()
        self.name = name
        self.doctrine = doctrine
        self.red_lines = red_lines
        self.persona = persona
        self.llm = llm

    async def act(
        self,
        perception: Perception,
        memory: list[MemoryRecord] | None = None,
    ) -> ProposedAction:
        """Run a single decision cycle and return the proposed action.

        Args:
            perception: What the agent sees this turn.
            memory: Retrieved memory records (top-k from the memory store).

        Returns:
            A validated :class:`ProposedAction`.
        """
        memories = memory or perception.memories or []
        system_prompt = render_country_prompt(perception, memories)
        human_prompt = (
            f"Turn {perception.world_view.get('turn', 0)}: propose your action. "
            f"Call exactly one tool."
        )

        # Filter out disabled tools (per-demo slim via DISABLED_AGENT_TOOLS env).
        # Each tool schema costs ~300-500 input tokens, so dropping unused tools
        # materially reduces per-call token burn. Default drops cyber + info
        # ops since they're rare in a Taiwan quarantine slice.
        _disabled = {
            name.strip()
            for name in os.environ.get(
                "DISABLED_AGENT_TOOLS", "information_action,cyber_action"
            ).split(",")
            if name.strip()
        }
        tools = [t for t in COUNTRY_AGENT_TOOLS if t["name"] not in _disabled]

        response = await self.llm.ainvoke_tools(
            system_prompt=system_prompt,
            human_prompt=human_prompt,
            tools=tools,
        )

        if not response.tool_calls:
            log.warning(
                "country_agent_no_tool_call",
                actor=self.code,
                content_preview=response.content[:120],
            )
            return ProposedAction(
                actor=self.code,
                target=None,
                domain=Domain.diplomatic,
                action_type="no_action",
                payload={"reason": "LLM did not call a tool"},
                rationale=response.content[:500],
                estimated_escalation_rung=0,
            )

        call = response.tool_calls[0]
        try:
            action = tool_call_to_action(self.code, call)
        except ValueError as e:
            log.error("country_agent_bad_tool_call", actor=self.code, error=str(e))
            action = ProposedAction(
                actor=self.code,
                target=None,
                domain=Domain.diplomatic,
                action_type="no_action",
                payload={"reason": str(e)},
                rationale="Fell back to no_action due to invalid tool call.",
                estimated_escalation_rung=0,
            )

        # Defensive: never let an agent target itself
        if action.target == self.code:
            action = action.model_copy(update={"target": None})

        return action


# ---------------------------------------------------------------------------
# Real LangChain LLM adapter (optional — used in production)
# ---------------------------------------------------------------------------


class ChatAnthropicClient:
    """Thin adapter around ``langchain_anthropic.ChatAnthropic`` for tool-use.

    Kept in a separate class so the core agent logic can be tested without
    pulling in langchain/anthropic at import time.
    """

    def __init__(self, model: str, api_key: str, temperature: float = 0.3) -> None:
        # We used to wrap langchain_anthropic.ChatAnthropic, but that adapter
        # silently strips the ``cache_control`` key from content blocks when
        # it serialises SystemMessage for the outbound request — confirmed by
        # inspecting the actual /v1/messages payload (no cache_control field)
        # and the absence of ``cache_read_input_tokens`` in responses.  The
        # direct anthropic SDK preserves the marker and returns cache-hit
        # usage telemetry so we can verify it's working.
        #
        # Lazy-import so unit tests that never hit the real client don't need
        # the anthropic dep imported.
        from anthropic import AsyncAnthropic  # type: ignore[import-not-found]

        self._client: Any = AsyncAnthropic(api_key=api_key)
        self._model = model
        self._temperature = temperature
        # max_tokens trimmed for demo — country agents emit one tool-call per
        # turn (a small JSON object plus 2-4 sentences of rationale). 2048 was
        # massive overkill and cost meaningful ITPM budget.  Override via
        # AGENT_MAX_TOKENS env if reasoning feels truncated.
        self._max_tokens = int(os.environ.get("AGENT_MAX_TOKENS", "512"))

    @staticmethod
    def _build_cached_tools(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Attach cache_control to the LAST tool in the list.

        Anthropic's semantics: cache_control on a tool caches every tool up
        to and including that one.  So one marker on the final tool caches
        the entire tools array — ~1k tokens that would otherwise re-bill on
        every single turn.  Safe to mutate via dict-copy; the global
        COUNTRY_AGENT_TOOLS constant stays untouched.
        """
        if not tools:
            return tools
        cached = [dict(t) for t in tools]
        cached[-1] = {**cached[-1], "cache_control": {"type": "ephemeral"}}
        return cached

    async def ainvoke_tools(
        self,
        system_prompt: str,
        human_prompt: str,
        tools: list[dict[str, Any]],
    ) -> LLMResponse:
        """Invoke the chat model with the given tool schemas bound.

        Outbound request shape:
          * ``system`` — list of two text blocks.  The first carries
            ``cache_control=ephemeral`` and holds the stable persona /
            OCEAN / doctrine / rules prefix.  The second holds the
            volatile intel / posture / memory / world-view tail.
          * ``tools`` — last tool carries ``cache_control=ephemeral`` so
            Anthropic caches the entire tools array across turns.

        On cache hits the response ``usage`` block reports a non-zero
        ``cache_read_input_tokens``; the INPUT TPM budget is charged at
        ~10% of the cached portion, which is what relieves rate-limit
        pressure during the 7-agent fan-out.
        """
        cacheable_prefix, volatile_suffix = _split_for_cache(system_prompt)
        system_blocks: list[dict[str, Any]] = [
            {
                "type": "text",
                "text": cacheable_prefix,
                "cache_control": {"type": "ephemeral"},
            }
        ]
        if volatile_suffix:
            system_blocks.append({"type": "text", "text": volatile_suffix})

        response = await self._client.messages.create(
            model=self._model,
            max_tokens=self._max_tokens,
            temperature=self._temperature,
            system=system_blocks,
            tools=self._build_cached_tools(tools),
            messages=[{"role": "user", "content": human_prompt}],
        )

        # Emit cache telemetry at DEBUG — lets us confirm caching works by
        # grepping logs for non-zero cache_read_input_tokens.
        usage = getattr(response, "usage", None)
        if usage is not None:
            log.debug(
                "agent_llm_usage",
                input_tokens=getattr(usage, "input_tokens", 0),
                output_tokens=getattr(usage, "output_tokens", 0),
                cache_creation_input_tokens=getattr(
                    usage, "cache_creation_input_tokens", 0
                ),
                cache_read_input_tokens=getattr(usage, "cache_read_input_tokens", 0),
            )

        tool_calls: list[ToolCall] = []
        content_text_parts: list[str] = []
        for block in response.content:
            block_type = getattr(block, "type", None)
            if block_type == "tool_use":
                tool_calls.append(
                    ToolCall(
                        name=getattr(block, "name", "") or "",
                        args=dict(getattr(block, "input", {}) or {}),
                        id=getattr(block, "id", "") or "",
                    )
                )
            elif block_type == "text":
                content_text_parts.append(getattr(block, "text", "") or "")

        return LLMResponse(
            tool_calls=tool_calls,
            content=" ".join(p for p in content_text_parts if p),
        )
