/**
 * TypeScript mirror of src/shared/schemas/ws_frame.py
 * Discriminated union on `frame_type` matches the Python WsFrame exactly.
 */

import type { SimEvent } from './sim-event';

/** All frame type discriminants. */
export type FrameType =
  | 'connected'
  | 'turn_start'
  | 'sim_event'
  | 'turn_end'
  | 'sim_complete'
  | 'error'
  | 'heartbeat'
  | 'control';

/** Control actions the client can send to the server. */
export type ControlAction = 'pause' | 'resume' | 'abort';

// ── Server → Client payload shapes ────────────────────────────────────────

export interface ConnectedPayload {
  frame_type: 'connected';
  sim_id: string;
  status: string;
  current_turn: number;
  max_turns: number;
  countries: string[];
}

export interface TurnStartPayload {
  frame_type: 'turn_start';
  turn: number;
  world_state: Record<string, unknown>;
}

export interface SimEventPayload {
  frame_type: 'sim_event';
  event: SimEvent;
}

export interface TurnEndPayload {
  frame_type: 'turn_end';
  turn: number;
  events_count: number;
  relationship_deltas: Record<string, Record<string, number>>;
  max_escalation_rung_this_turn: number;
}

export interface SimCompletePayload {
  frame_type: 'sim_complete';
  status: string;
  total_turns: number;
  total_events: number;
  final_world_state: Record<string, unknown>;
  peak_escalation_rung: number;
  outcome_summary: string;
}

export interface ErrorPayload {
  frame_type: 'error';
  code: string;
  message: string;
  recoverable: boolean;
  turn: number | null;
}

export interface HeartbeatPayload {
  frame_type: 'heartbeat';
  status: string;
  current_turn: number;
}

export interface ControlPayload {
  frame_type: 'control';
  action: ControlAction;
}

/** Union of all server→client payloads, discriminated by `frame_type`. */
export type AnyServerPayload =
  | ConnectedPayload
  | TurnStartPayload
  | SimEventPayload
  | TurnEndPayload
  | SimCompletePayload
  | ErrorPayload
  | HeartbeatPayload;

/**
 * Outer envelope for all server → client WebSocket messages.
 * Mirrors WsFrame (Python).
 */
export interface WsFrame {
  frame_type: FrameType;
  sim_id: string;
  seq: number;
  ts: string; // ISO-8601
  payload: AnyServerPayload;
}

/**
 * Outer envelope for client → server control messages.
 * Mirrors WsControlFrame (Python).
 */
export interface WsControlFrame {
  frame_type: 'control';
  sim_id: string;
  seq: number;
  ts: string; // ISO-8601
  payload: ControlPayload;
}
