/**
 * TypeScript mirror of src/shared/schemas/scenario.py
 */

export type ScenarioStatus = 'draft' | 'ready' | 'archived';

/**
 * One structured event applied to the world at turn 0 before any agents
 * run.  Mirrors src/shared/schemas/scenario.py::SeedEvent.  Keeping the
 * shape strict on the frontend so preset authors get type-checking on
 * every seeded event (misspelled domain / missing actor fails at compile).
 */
export interface SeedEvent {
  actor_country: string;
  target_country: string | null;
  domain:
    | 'info'
    | 'diplomatic'
    | 'economic'
    | 'cyber'
    | 'kinetic_limited'
    | 'kinetic_general';
  action_type: string;
  rationale: string;
  payload: Record<string, unknown>;
  escalation_rung: 0 | 1 | 2 | 3 | 4 | 5;
}

export interface InitialConditions {
  posture_overrides: Record<string, string>;
  seed_events: SeedEvent[];
}

/** POST /api/scenarios request body. */
export interface ScenarioCreate {
  title: string;
  description: string;
  country_ids: string[];
  initial_conditions?: Partial<InitialConditions>;
}

/** Full scenario object returned by POST and GET /api/scenarios/{id}. */
export interface ScenarioResponse {
  id: string;
  title: string;
  description: string;
  country_ids: string[];
  initial_conditions: Record<string, unknown>;
  status: ScenarioStatus;
  created_at: string;
  updated_at: string;
}

/** Lightweight item in GET /api/scenarios list. */
export interface ScenarioListItem {
  id: string;
  title: string;
  status: ScenarioStatus;
  country_ids: string[];
  created_at: string;
}

export interface ScenarioList {
  items: ScenarioListItem[];
  total: number;
  limit: number;
  offset: number;
}

/** POST /api/simulations request body. */
export interface SimulationCreate {
  scenario_id: string;
  max_turns?: number;
  config?: {
    model?: string;
    arbiter_model?: string;
    random_seed?: number;
  };
}

/**
 * Mirrors backend SimulationStatus enum exactly.
 * 'idle' is intentionally absent — the backend never emits it.
 * Frontend pre-start state is represented by simStatus === 'pending' or
 * by the separate `playbackReady` boolean in simStore.
 */
export type SimulationStatus =
  | 'pending'
  | 'running'
  | 'paused'
  | 'completed'
  | 'aborted'
  | 'error';

export interface SimulationResponse {
  id: string;
  scenario_id: string;
  status: SimulationStatus;
  current_turn: number;
  max_turns: number;
  world_state_snapshot: Record<string, unknown> | null;
  config: Record<string, unknown>;
  created_at: string;
  started_at: string | null;
  completed_at: string | null;
  ws_url: string;
}

// ── Free-form scenario extraction ─────────────────────────────────────────
//
// Mirrors src/shared/schemas/scenario.py SelectedCountry / ExtractEvents*.

/**
 * One country chosen to participate in a free-form scenario.
 * `relevance_score` is ordinal within one response, not comparable across
 * calls. `source`-aware: the tooltip should render the rationale verbatim.
 */
export interface SelectedCountry {
  iso3: string;
  relevance_score: number;
  rationale: string;
}

export interface ExtractEventsRequest {
  description: string;
  pinned_country_ids?: string[];
}

/**
 * Backend response for POST /api/scenarios/extract-events.
 *
 * `source` tells the UI whether the LLM carried the load (`llm`), the
 * deterministic scorer topped up a partial LLM response (`partial_fallback`),
 * or the scorer ran end-to-end without LLM help (`fallback`). The
 * confirmation card surfaces this as a subtle badge so users know what
 * produced the selection.
 */
export interface ExtractEventsResponse {
  seed_events: SeedEvent[];
  selected_countries: SelectedCountry[];
  posture_overrides: Record<string, string>;
  source: 'llm' | 'fallback' | 'partial_fallback';
}

export type ComposerMode = 'preset' | 'freeform';
