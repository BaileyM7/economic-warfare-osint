"""Shared Pydantic v2 schemas.

Import from here for cross-package usage:
    from wargame_shared.schemas import SimEvent, WsFrame, ScenarioCreate
"""

from wargame_shared.schemas.scenario import (
    ScenarioCreate,
    ScenarioListItem,
    ScenarioResponse,
    ScenarioStatus,
)
from wargame_shared.schemas.sim_event import (
    Citation,
    Domain,
    EscalationRung,
    SimEvent,
    SimEventCreate,
)
from wargame_shared.schemas.ws_frame import (
    ConnectedPayload,
    ControlAction,
    ControlPayload,
    ErrorPayload,
    FrameType,
    HeartbeatPayload,
    SimCompletePayload,
    SimEventPayload,
    TurnEndPayload,
    TurnStartPayload,
    WsControlFrame,
    WsFrame,
)

__all__ = [
    # sim_event
    "Citation",
    "Domain",
    "EscalationRung",
    "SimEvent",
    "SimEventCreate",
    # scenario
    "ScenarioCreate",
    "ScenarioListItem",
    "ScenarioResponse",
    "ScenarioStatus",
    # ws_frame
    "FrameType",
    "ControlAction",
    "ConnectedPayload",
    "TurnStartPayload",
    "SimEventPayload",
    "TurnEndPayload",
    "SimCompletePayload",
    "ErrorPayload",
    "HeartbeatPayload",
    "ControlPayload",
    "WsFrame",
    "WsControlFrame",
]
