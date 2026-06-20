/**
 * Typed fetch wrapper for the Swarm REST API.
 * Base URL read from VITE_SWARM_API_URL (defaults to http://localhost:8000).
 */

import type {
  Country,
  CountryList,
} from '@/lib/types/country';
import type {
  ExtractEventsRequest,
  ExtractEventsResponse,
  ScenarioCreate,
  ScenarioResponse,
  ScenarioList,
  SimulationCreate,
  SimulationResponse,
} from '@/lib/types/scenario';
import type { SimEvent } from '@/lib/types/sim-event';

// Default to the same-origin `/api/wargame` path so the embedded swarm
// subapp (see src/api.py) answers directly. Override via VITE_SWARM_API_URL
// if you want to point at a separately-deployed swarm backend.
const BASE_URL =
  (import.meta.env.VITE_SWARM_API_URL as string | undefined)?.replace(/\/$/, '') ?? '/api/wargame';

// ── Generic API response envelope ─────────────────────────────────────────

interface ApiSuccess<T> {
  data: T;
  error: null;
}

interface ApiError {
  data: null;
  error: {
    code: string;
    message: string;
    details?: { field: string; issue: string }[];
  };
}

type ApiResponse<T> = ApiSuccess<T> | ApiError;

// ── Error class ───────────────────────────────────────────────────────────

export class ApiRequestError extends Error {
  constructor(
    public readonly code: string,
    message: string,
    public readonly status: number,
    public readonly details?: { field: string; issue: string }[],
  ) {
    super(message);
    this.name = 'ApiRequestError';
  }
}

// ── Core fetch helper ─────────────────────────────────────────────────────

async function apiFetch<T>(
  path: string,
  init?: RequestInit,
): Promise<T> {
  const url = `${BASE_URL}${path}`;
  const res = await fetch(url, {
    headers: { 'Content-Type': 'application/json', ...init?.headers },
    ...init,
  });

  const envelope = (await res.json()) as ApiResponse<T>;

  if (envelope.error) {
    throw new ApiRequestError(
      envelope.error.code,
      envelope.error.message,
      res.status,
      envelope.error.details,
    );
  }

  return envelope.data as T;
}

// ── Country endpoints ─────────────────────────────────────────────────────

export async function listCountries(params?: {
  iso3?: string;
  limit?: number;
  offset?: number;
}): Promise<CountryList> {
  const qs = new URLSearchParams();
  if (params?.iso3) qs.set('iso3', params.iso3);
  if (params?.limit != null) qs.set('limit', String(params.limit));
  if (params?.offset != null) qs.set('offset', String(params.offset));
  const query = qs.toString() ? `?${qs}` : '';
  return apiFetch<CountryList>(`/api/countries${query}`);
}

export async function getCountry(iso3: string): Promise<Country> {
  return apiFetch<Country>(`/api/countries/${encodeURIComponent(iso3)}`);
}

// ── Scenario endpoints ────────────────────────────────────────────────────

export async function listScenarios(params?: {
  status?: string;
  limit?: number;
  offset?: number;
}): Promise<ScenarioList> {
  const qs = new URLSearchParams();
  if (params?.status) qs.set('status', params.status);
  if (params?.limit != null) qs.set('limit', String(params.limit));
  if (params?.offset != null) qs.set('offset', String(params.offset));
  const query = qs.toString() ? `?${qs}` : '';
  return apiFetch<ScenarioList>(`/api/scenarios${query}`);
}

export async function getScenario(id: string): Promise<ScenarioResponse> {
  return apiFetch<ScenarioResponse>(`/api/scenarios/${encodeURIComponent(id)}`);
}

export async function createScenario(
  body: ScenarioCreate,
): Promise<ScenarioResponse> {
  return apiFetch<ScenarioResponse>('/api/scenarios', {
    method: 'POST',
    body: JSON.stringify(body),
  });
}

/**
 * Extract seed events + exactly 5 selected countries from a free-form
 * scenario description.
 *
 * Backend calls Claude Haiku with a forced-tool-use schema; falls back to
 * a deterministic scorer if the LLM fails or returns insufficient output.
 * `source` on the response tells the UI which path was taken so it can
 * render an appropriate provenance badge.
 */
export async function extractScenarioEvents(
  body: ExtractEventsRequest,
): Promise<ExtractEventsResponse> {
  return apiFetch<ExtractEventsResponse>('/api/scenarios/extract-events', {
    method: 'POST',
    body: JSON.stringify(body),
  });
}

// ── Simulation endpoints ──────────────────────────────────────────────────

export async function createSimulation(
  body: SimulationCreate,
): Promise<SimulationResponse> {
  return apiFetch<SimulationResponse>('/api/simulations', {
    method: 'POST',
    body: JSON.stringify(body),
  });
}

export async function getSimulation(id: string): Promise<SimulationResponse> {
  return apiFetch<SimulationResponse>(`/api/simulations/${encodeURIComponent(id)}`);
}

export async function abortSimulation(id: string): Promise<SimulationResponse> {
  // The architecture.md spec doesn't define a dedicated abort REST endpoint;
  // abort is sent as a WS control frame. This is a convenience polling method.
  return apiFetch<SimulationResponse>(
    `/api/simulations/${encodeURIComponent(id)}`,
  );
}

// ── Sim events (REST — post-run replay) ───────────────────────────────────

interface SimEventList {
  items: SimEvent[];
  next_cursor: string | null;
  has_more: boolean;
}

export async function listSimEvents(
  simId: string,
  params?: {
    turn?: number;
    actor_country?: string;
    domain?: string;
    limit?: number;
    cursor?: string;
  },
): Promise<SimEventList> {
  const qs = new URLSearchParams();
  if (params?.turn != null) qs.set('turn', String(params.turn));
  if (params?.actor_country) qs.set('actor_country', params.actor_country);
  if (params?.domain) qs.set('domain', params.domain);
  if (params?.limit != null) qs.set('limit', String(params.limit));
  if (params?.cursor) qs.set('cursor', params.cursor);
  const query = qs.toString() ? `?${qs}` : '';
  return apiFetch<SimEventList>(
    `/api/simulations/${encodeURIComponent(simId)}/events${query}`,
  );
}
