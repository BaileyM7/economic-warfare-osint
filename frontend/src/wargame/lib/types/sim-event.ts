/**
 * TypeScript mirror of src/shared/schemas/sim_event.py
 * Keep in sync with the Python Pydantic models.
 */

/** Domain classification — drives arc color on the globe. */
export type Domain =
  | 'info'             // light blue  #a9d8fb
  | 'diplomatic'       // blue        #4386c3  (mapped to info color in DESIGN.md)
  | 'economic'         // orange      #efb16a
  | 'cyber'            // blue        #4386c3
  | 'kinetic_limited'  // orange  (mapped to kinetic color)
  | 'kinetic_general'; // red         #d23c3a

/** Escalation ladder integer 0..5. */
export type EscalationRung = 0 | 1 | 2 | 3 | 4 | 5;

export const ESCALATION_LABELS: Record<EscalationRung, string> = {
  0: 'Peacetime',
  1: 'Gray Zone',
  2: 'Coercive Diplomacy',
  3: 'Limited Conflict',
  4: 'Regional War',
  5: 'General War',
};

/** Reference to a data-lake event cited by an agent. */
export interface Citation {
  source: string; // e.g. 'gdelt', 'acled', 'worldbank'
  ref: string;    // source-specific event reference ID
}

/** The class of evidence an agent cited as a triggering factor. */
export type FactorKind =
  | 'event'        // ref is a SimEvent UUID from the agent's perception
  | 'red_line'     // ref is a red-line slug (or first 6 words of description)
  | 'memory'       // ref is "turn:N" pointing at a recalled memory turn
  | 'posture'      // ref is an ordered ISO3 pair like "USA-TWN"
  | 'perception';  // ref is a dotted field path within the perception summary

/** One concrete piece of evidence the agent cited when deciding to act. */
export interface TriggeringFactor {
  kind: FactorKind;
  ref: string;
  note: string;
  /**
   * False when the SimLoop could not resolve `ref` against the actor's
   * recent perception (e.g. a `kind=event` ref that did not appear).
   * UI should render unverified factors muted.
   */
  verified: boolean;
}

/**
 * Structured explanation of a single agent decision. Reads as:
 *   actor did `summary` because of `triggering_factors` in hopes of `intended_outcome`.
 * Mirrors Explainability (Python).
 */
export interface Explainability {
  summary: string;
  triggering_factors: TriggeringFactor[];
  intended_outcome: string;
}

/**
 * Full SimEvent as stored in Postgres and streamed over WebSocket.
 * Mirrors SimEvent (Python) exactly.
 */
export interface SimEvent {
  id: string;                        // UUID
  sim_id: string;                    // UUID
  parent_event_id: string | null;    // UUID | null
  turn: number;                      // 0-indexed turn counter
  actor_country: string;             // ISO-3
  target_country: string | null;     // ISO-3 | null
  domain: Domain;
  action_type: string;               // verb-phrase slug
  payload: Record<string, unknown>;  // domain-specific details
  rationale: string;                 // LLM chain-of-thought
  citations: Citation[];
  escalation_rung: EscalationRung;
  /**
   * Structured explainability triplet. Null for legacy events emitted
   * before the feature shipped, and for scenario seed events.
   */
  explainability: Explainability | null;
  timestamp: string;                 // ISO-8601
}
