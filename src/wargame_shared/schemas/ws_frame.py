"""WebSocket frame envelope schema — discriminated union on `frame_type`.

All WebSocket messages (server → client and client → server) use this
envelope format.  The `frame_type` field is the discriminant that selects
the correct payload Pydantic model at parse time.

Usage (server, serialising):
    frame = WsFrame(
        frame_type=FrameType.sim_event,
        sim_id=sim_id,
        seq=seq,
        payload=SimEventPayload(event=sim_event),
    )
    await websocket.send_text(frame.model_dump_json())

Usage (client, parsing):
    frame = WsFrame.model_validate_json(raw_text)
    if frame.frame_type == FrameType.sim_event:
        assert isinstance(frame.payload, SimEventPayload)
        handle_sim_event(frame.payload.event)
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from wargame_shared.schemas.sim_event import SimEvent


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class FrameType(str, Enum):
    """Discriminant for WebSocket frame type."""

    connected = "connected"          # S→C: sent immediately on WS upgrade
    turn_start = "turn_start"        # S→C: beginning of a new turn
    sim_event = "sim_event"          # S→C: agent action event
    turn_end = "turn_end"            # S→C: all agents in a turn have acted
    sim_complete = "sim_complete"    # S→C: simulation finished
    error = "error"                  # S→C: server-side error
    heartbeat = "heartbeat"          # S→C: keepalive (every 15 s of idle)
    control = "control"              # C→S: pause / resume / abort


class ControlAction(str, Enum):
    """Actions available in a client → server control frame."""

    pause = "pause"
    resume = "resume"
    abort = "abort"


# ---------------------------------------------------------------------------
# Payload models (one per frame_type)
# ---------------------------------------------------------------------------


class ConnectedPayload(BaseModel):
    """Sent immediately after a successful WebSocket upgrade.

    If the simulation is already running or completed, `current_turn` and
    `status` reflect the live state so the client can catch up.
    """

    model_config = ConfigDict(frozen=True)

    frame_type: Literal[FrameType.connected] = FrameType.connected
    sim_id: uuid.UUID
    status: str = Field(..., description="Current simulation status string.")
    current_turn: int = Field(..., ge=0)
    max_turns: int = Field(..., ge=1)
    countries: list[str] = Field(
        ..., description="ISO-3 codes of all countries in this simulation."
    )


class TurnStartPayload(BaseModel):
    """Marks the beginning of a new simulation turn.

    Includes a snapshot of current world state so the globe can update
    relationship edges before agent events arrive.
    """

    model_config = ConfigDict(frozen=True)

    frame_type: Literal[FrameType.turn_start] = FrameType.turn_start
    turn: int = Field(..., ge=0)
    world_state: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "Current world state snapshot including relationship postures "
            "and trust scores, keyed as 'ISO3A-ISO3B'."
        ),
    )


class SimEventPayload(BaseModel):
    """Wraps a full SimEvent for transmission over WebSocket."""

    model_config = ConfigDict(frozen=True)

    frame_type: Literal[FrameType.sim_event] = FrameType.sim_event
    event: SimEvent


class TurnEndPayload(BaseModel):
    """Summarises the outcome of a completed turn."""

    model_config = ConfigDict(frozen=True)

    frame_type: Literal[FrameType.turn_end] = FrameType.turn_end
    turn: int = Field(..., ge=0)
    events_count: int = Field(..., ge=0, description="Number of sim_events emitted this turn.")
    relationship_deltas: dict[str, dict[str, float]] = Field(
        default_factory=dict,
        description=(
            "Map of 'ISO3A-ISO3B' → {'trust_score_delta': float} "
            "for all pairs affected this turn."
        ),
    )
    max_escalation_rung_this_turn: int = Field(
        default=0,
        ge=0,
        le=5,
        description="Highest escalation rung reached by any event this turn.",
    )


class SimCompletePayload(BaseModel):
    """Emitted when the simulation reaches its terminal state."""

    model_config = ConfigDict(frozen=True)

    frame_type: Literal[FrameType.sim_complete] = FrameType.sim_complete
    status: str = Field(..., description="Terminal status: 'completed' or 'aborted'.")
    total_turns: int = Field(..., ge=0)
    total_events: int = Field(..., ge=0)
    final_world_state: dict[str, Any] = Field(default_factory=dict)
    peak_escalation_rung: int = Field(default=0, ge=0, le=5)
    outcome_summary: str = Field(
        default="",
        description="One-sentence LLM-generated outcome summary.",
    )


class ErrorPayload(BaseModel):
    """Server-side error notification.

    If `recoverable` is True, the client should wait and reconnect.
    If False, the simulation has terminated and cannot be resumed.
    """

    model_config = ConfigDict(frozen=True)

    frame_type: Literal[FrameType.error] = FrameType.error
    code: str = Field(..., description="Machine-readable error code.")
    message: str = Field(..., description="Human-readable description.")
    recoverable: bool = Field(
        default=True,
        description="Whether the simulation can continue after this error.",
    )
    turn: int | None = Field(default=None, description="Turn when the error occurred.")


class HeartbeatPayload(BaseModel):
    """Keepalive frame sent every 15 seconds of idle to prevent connection drop."""

    model_config = ConfigDict(frozen=True)

    frame_type: Literal[FrameType.heartbeat] = FrameType.heartbeat
    status: str = Field(..., description="Current simulation status.")
    current_turn: int = Field(..., ge=0)


class ControlPayload(BaseModel):
    """Client → server control command."""

    model_config = ConfigDict(frozen=True)

    frame_type: Literal[FrameType.control] = FrameType.control
    action: ControlAction


# ---------------------------------------------------------------------------
# Discriminated union type aliases
# ---------------------------------------------------------------------------

# Union of all server→client payload types (used as the `payload` field type in WsFrame)
AnyServerPayload = Annotated[
    ConnectedPayload
    | TurnStartPayload
    | SimEventPayload
    | TurnEndPayload
    | SimCompletePayload
    | ErrorPayload
    | HeartbeatPayload,
    Field(discriminator="frame_type"),
]


# ---------------------------------------------------------------------------
# Frame envelope (server → client)
# ---------------------------------------------------------------------------


class WsFrame(BaseModel):
    """Outer envelope for all server → client WebSocket messages.

    The `frame_type` on the envelope and on the `payload` are kept in sync;
    `payload` is a discriminated union so Pydantic picks the correct model.
    """

    model_config = ConfigDict(
        populate_by_name=True,
    )

    frame_type: FrameType = Field(..., description="Discriminant for payload type.")
    sim_id: uuid.UUID = Field(..., description="Owning simulation UUID.")
    seq: int = Field(..., ge=0, description="Monotonically increasing per connection.")
    ts: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="Server-side UTC timestamp.",
    )
    payload: AnyServerPayload = Field(..., description="Typed payload discriminated by frame_type.")


# ---------------------------------------------------------------------------
# Control frame envelope (client → server)
# ---------------------------------------------------------------------------


class WsControlFrame(BaseModel):
    """Outer envelope for client → server control messages."""

    model_config = ConfigDict(populate_by_name=True)

    frame_type: Literal[FrameType.control] = FrameType.control
    sim_id: uuid.UUID
    seq: int = Field(..., ge=0)
    ts: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    payload: ControlPayload
