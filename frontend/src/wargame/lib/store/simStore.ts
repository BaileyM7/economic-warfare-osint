/**
 * Global Zustand store — single source of truth for simulation state.
 * Holds: current scenario, current simulation id, world snapshot,
 *         events[], selectedCountry, selectedEvent, playback state,
 *         turn-scrubber position.
 */

import { create } from 'zustand';
import type { SimEvent } from '@/lib/types/sim-event';
import type {
  ComposerMode,
  ExtractEventsResponse,
  ScenarioResponse,
  SeedEvent,
  SimulationStatus,
} from '@/lib/types/scenario';

// Sentinel indicating no simulation has been started yet (client-only concept,
// not emitted by the backend). Components should check `playbackReady` instead.
export const NO_SIM_STATUS = null;

export interface SimState {
  // ── Scenario ──────────────────────────────────────────────────────────
  currentScenario: ScenarioResponse | null;
  setCurrentScenario: (scenario: ScenarioResponse | null) => void;

  // ── Simulation ────────────────────────────────────────────────────────
  currentSimId: string | null;
  setCurrentSimId: (id: string | null) => void;

  /**
   * null = no simulation started yet (pre-start, client-only).
   * Once a simulation is created the backend status drives this field.
   */
  simStatus: SimulationStatus | null;
  setSimStatus: (status: SimulationStatus | null) => void;

  currentTurn: number;
  setCurrentTurn: (turn: number) => void;

  maxTurns: number;
  setMaxTurns: (max: number) => void;

  // ── Events ────────────────────────────────────────────────────────────
  events: SimEvent[];
  addEvent: (event: SimEvent) => void;
  clearEvents: () => void;

  // ── World state ───────────────────────────────────────────────────────
  worldSnapshot: Record<string, unknown>;
  setWorldSnapshot: (snapshot: Record<string, unknown>) => void;

  // ── Selection ─────────────────────────────────────────────────────────
  selectedCountry: string | null;
  setSelectedCountry: (iso3: string | null) => void;

  selectedEvent: SimEvent | null;
  setSelectedEvent: (event: SimEvent | null) => void;

  // ── View Mode ─────────────────────────────────────────────────────────
  /** Which visualization to show in the center column. */
  viewMode: 'globe' | 'map';
  setViewMode: (mode: 'globe' | 'map') => void;

  // ── Playback ──────────────────────────────────────────────────────────
  /** 'live' = following latest turn; 'scrubbing' = user has scrubbed to a past turn */
  playbackMode: 'live' | 'scrubbing';
  setPlaybackMode: (mode: 'live' | 'scrubbing') => void;

  /** The turn the user has scrubbed to (only relevant in 'scrubbing' mode). */
  scrubberTurn: number;
  setScrubberTurn: (turn: number) => void;

  // ── Composer (free-form vs preset) ───────────────────────────────────
  /**
   * Which scenario-composition mode the user is in. 'preset' keeps the
   * existing byte-identical preset flow; 'freeform' opens the Analyze →
   * Confirm → Execute path backed by /api/scenarios/extract-events.
   */
  composerMode: ComposerMode;
  setComposerMode: (mode: ComposerMode) => void;

  /**
   * Last extraction result from the backend, cached in the store so it
   * survives minor tree re-mounts but is wiped on reset(). null until the
   * user clicks Analyze on a free-form prompt.
   */
  extractionResult: ExtractEventsResponse | null;
  setExtractionResult: (result: ExtractEventsResponse | null) => void;

  /**
   * User edits to the extraction result before Execute. If null, the
   * extraction result's own selected_countries / seed_events are used.
   * Diff stored as the edited list, not a delta — simpler to reason about.
   */
  editedSelectedCountries: string[] | null;
  setEditedSelectedCountries: (isos: string[] | null) => void;

  editedSeedEvents: SeedEvent[] | null;
  setEditedSeedEvents: (events: SeedEvent[] | null) => void;

  // ── Utilities ─────────────────────────────────────────────────────────
  /** Full reset — called when starting a new simulation. */
  reset: () => void;

  /** Events filtered to the scrubber turn (or all events in live mode). */
  visibleEvents: () => SimEvent[];
}

const initialState = {
  currentScenario: null,
  currentSimId: null,
  simStatus: null as SimulationStatus | null,
  currentTurn: 0,
  maxTurns: 20,
  events: [] as SimEvent[],
  worldSnapshot: {} as Record<string, unknown>,
  selectedCountry: null,
  selectedEvent: null,
  viewMode: 'globe' as const,
  playbackMode: 'live' as const,
  scrubberTurn: 0,
  composerMode: 'preset' as ComposerMode,
  extractionResult: null as ExtractEventsResponse | null,
  editedSelectedCountries: null as string[] | null,
  editedSeedEvents: null as SeedEvent[] | null,
};

export const useSimStore = create<SimState>((set, get) => ({
  ...initialState,

  setCurrentScenario: (scenario) => set({ currentScenario: scenario }),

  setCurrentSimId: (id) => set({ currentSimId: id }),

  setSimStatus: (status) => set({ simStatus: status }),

  setCurrentTurn: (turn) =>
    set((s) => ({
      currentTurn: turn,
      // Auto-update scrubber when in live mode
      scrubberTurn: s.playbackMode === 'live' ? turn : s.scrubberTurn,
    })),

  setMaxTurns: (max) => set({ maxTurns: max }),

  addEvent: (event) =>
    set((s) => ({
      events: [...s.events, event],
    })),

  clearEvents: () => set({ events: [] }),

  setWorldSnapshot: (snapshot) => set({ worldSnapshot: snapshot }),

  setSelectedCountry: (iso3) => set({ selectedCountry: iso3 }),

  setSelectedEvent: (event) => set({ selectedEvent: event }),

  setViewMode: (mode) => set({ viewMode: mode }),

  setPlaybackMode: (mode) => set({ playbackMode: mode }),

  setScrubberTurn: (turn) =>
    set({ scrubberTurn: turn, playbackMode: 'scrubbing' }),

  setComposerMode: (mode) => set({ composerMode: mode }),

  setExtractionResult: (result) =>
    set({
      extractionResult: result,
      // Whenever a fresh extraction arrives, reset any pending edits — the
      // user's edits only make sense against the result they were looking at.
      editedSelectedCountries: null,
      editedSeedEvents: null,
    }),

  setEditedSelectedCountries: (isos) => set({ editedSelectedCountries: isos }),

  setEditedSeedEvents: (events) => set({ editedSeedEvents: events }),

  reset: () =>
    set({
      ...initialState,
      // Preserve scenario + composer mode so user doesn't have to re-pick
      currentScenario: get().currentScenario,
      composerMode: get().composerMode,
    }),

  visibleEvents: () => {
    const { events, playbackMode, scrubberTurn } = get();
    if (playbackMode === 'live') return events;
    return events.filter((e) => e.turn <= scrubberTurn);
  },
}));
