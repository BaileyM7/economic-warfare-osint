import type {
  HealthResponse,
  SanctionsImpactResponse,
  EntityGraphResponse,
  EntityResolutionResponse,
  PersonProfileResponse,
  PersonCandidate,
  PersonNetworkResponse,
  SectorAnalysisResponse,
  VesselTrackResponse,
  OrchestratorStatusResponse,
  StartAnalysisResponse,
  SuggestResponse,
  SayariResolveResponse,
  SayariTraversalResponse,
  SayariUBOResponse,
  EntityRiskReport,
  SanctionsScreenBatchResponse,
  COA,
  KPIData,
  ActivityEntry,
  MapMarker,
  MacroData,
  Briefing,
  SubscribeResponse,
  SendBriefNowResponse,
} from './types';

const API_BASE = ((import.meta.env.VITE_API_BASE_URL as string | undefined) || '').replace(
  /\/$/,
  '',
);

// --- Auth helpers ---

const TOKEN_KEY = 'emissary_token';

export function getToken(): string | null {
  return localStorage.getItem(TOKEN_KEY);
}

export function setToken(token: string): void {
  localStorage.setItem(TOKEN_KEY, token);
}

export function clearToken(): void {
  localStorage.removeItem(TOKEN_KEY);
}

function authHeaders(): Record<string, string> {
  const token = getToken();
  return token ? { Authorization: `Bearer ${token}` } : {};
}

async function authedFetch(url: string, init: RequestInit = {}): Promise<Response> {
  const headers = { ...authHeaders(), ...((init.headers as Record<string, string>) || {}) };
  const res = await fetch(url, { ...init, headers });
  if (res.status === 401) {
    clearToken();
    // Redirect to login (avoid infinite loop during login itself)
    if (!url.includes('/auth/login') && window.location.pathname !== '/login') {
      window.location.href = '/login';
    }
  }
  return res;
}

async function parseJson<T>(res: Response): Promise<T> {
  const text = await res.text();
  if (!text) throw new Error(`Server returned empty response (HTTP ${res.status})`);
  try {
    const data = JSON.parse(text);
    if (!res.ok)
      throw new Error(data.detail || data.message || `Request failed (HTTP ${res.status})`);
    return data as T;
  } catch (e) {
    if (e instanceof SyntaxError) {
      throw new Error(`Server returned non-JSON response (HTTP ${res.status})`);
    }
    throw e;
  }
}

export async function fetchHealth(): Promise<HealthResponse> {
  const url = `${API_BASE}/api/health`;
  const res = await authedFetch(url);
  return parseJson<HealthResponse>(res);
}

export async function fetchSanctionsImpact(
  ticker: string,
  analystQuestion = '',
): Promise<SanctionsImpactResponse> {
  const url = `${API_BASE}/api/sanctions-impact`;
  const res = await authedFetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ ticker, analyst_question: analystQuestion }),
  });
  return parseJson<SanctionsImpactResponse>(res);
}

export async function fetchEntityGraph(query: string): Promise<EntityGraphResponse> {
  const url = `${API_BASE}/api/entity-graph`;
  const res = await authedFetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ query }),
  });
  return parseJson<EntityGraphResponse>(res);
}

export async function resolveEntity(query: string): Promise<EntityResolutionResponse> {
  const url = `${API_BASE}/api/resolve-entity`;
  const res = await authedFetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ query }),
  });
  return parseJson<EntityResolutionResponse>(res);
}

export async function fetchPersonProfile(
  name: string,
  analystQuestion = '',
): Promise<PersonProfileResponse> {
  const url = `${API_BASE}/api/person-profile`;
  const res = await authedFetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name, analyst_question: analystQuestion }),
  });
  return parseJson<PersonProfileResponse>(res);
}

export async function searchPersons(
  query: string,
  limit = 10,
  signal?: AbortSignal,
): Promise<PersonCandidate[]> {
  const url = `${API_BASE}/api/person/search`;
  const res = await authedFetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ query, limit }),
    signal,
  });
  return parseJson<PersonCandidate[]>(res);
}

export async function fetchPersonNetwork(
  name: string,
  depth: 1 | 2 = 1,
  maxPerNode = 15,
): Promise<PersonNetworkResponse> {
  const url = `${API_BASE}/api/person/network`;
  const res = await authedFetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name, depth, max_per_node: maxPerNode }),
  });
  return parseJson<PersonNetworkResponse>(res);
}

export async function fetchSectorAnalysis(
  sector: string,
  analystQuestion = '',
): Promise<SectorAnalysisResponse> {
  const url = `${API_BASE}/api/sector-analysis`;
  const res = await authedFetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ sector, analyst_question: analystQuestion }),
  });
  return parseJson<SectorAnalysisResponse>(res);
}

export async function startOrchestratorAnalysis(query: string): Promise<StartAnalysisResponse> {
  const url = `${API_BASE}/api/analyze`;
  const res = await authedFetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ query }),
  });
  return parseJson<StartAnalysisResponse>(res);
}

// Ask the backend whether a near-but-not-exact question has a fast-replay match.
// Returns {suggestion: null} for unrelated or exact queries — the caller only
// surfaces a non-null suggestion as a confirm prompt, never auto-runs it.
export async function suggestAnalysis(query: string): Promise<SuggestResponse> {
  const url = `${API_BASE}/api/analyze/suggest`;
  const res = await authedFetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ query }),
  });
  return parseJson<SuggestResponse>(res);
}

export async function pollAnalysisStatus(analysisId: string): Promise<OrchestratorStatusResponse> {
  const url = `${API_BASE}/api/analyze/${analysisId}`;
  const res = await authedFetch(url);
  return parseJson<OrchestratorStatusResponse>(res);
}

export async function fetchVesselTrack(
  query: string,
  analystQuestion = '',
): Promise<VesselTrackResponse> {
  const url = `${API_BASE}/api/vessel-track`;
  const res = await authedFetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ query, analyst_question: analystQuestion }),
  });
  return parseJson<VesselTrackResponse>(res);
}

// --- Sayari ---

export async function fetchSayariResolve(
  query: string,
  entityType?: string,
): Promise<SayariResolveResponse> {
  const url = `${API_BASE}/api/sayari/resolve`;
  const body: Record<string, unknown> = { query };
  if (entityType) body.entity_type = entityType;
  const res = await authedFetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  return parseJson<SayariResolveResponse>(res);
}

export async function fetchSayariRelated(
  entityId: string,
  depth = 1,
  limit = 20,
): Promise<SayariTraversalResponse> {
  const url = `${API_BASE}/api/sayari/related`;
  const res = await authedFetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ entity_id: entityId, depth, limit }),
  });
  return parseJson<SayariTraversalResponse>(res);
}

export async function fetchEntityRiskReport(
  name: string,
  entityType: string,
  ticker?: string,
  lei?: string,
): Promise<EntityRiskReport> {
  const url = `${API_BASE}/api/entity-risk-report`;
  const body: Record<string, unknown> = { name, entity_type: entityType };
  if (ticker) body.ticker = ticker;
  if (lei) body.lei = lei;
  const res = await authedFetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  return parseJson<EntityRiskReport>(res);
}

export async function fetchSanctionsScreenBatch(
  names: string[],
): Promise<SanctionsScreenBatchResponse> {
  const url = `${API_BASE}/api/sanctions/screen-batch`;
  const res = await authedFetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ names }),
  });
  return parseJson<SanctionsScreenBatchResponse>(res);
}

export async function fetchFollowUp(
  question: string,
  contextType: string,
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  context: Record<string, any>,
  history: { role: 'user' | 'assistant'; text: string }[] = [],
): Promise<{ answer: string }> {
  const url = `${API_BASE}/api/followup`;
  const res = await authedFetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ question, context_type: contextType, context, history }),
  });
  return parseJson<{ answer: string }>(res);
}

// --- Self-serve briefs (Phase 3) ---

export async function subscribeBriefs(
  email: string,
  phone?: string,
): Promise<SubscribeResponse> {
  const url = `${API_BASE}/api/subscribe`;
  const res = await authedFetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(phone ? { email, phone } : { email }),
  });
  return parseJson<SubscribeResponse>(res);
}

export async function sendBriefNow(): Promise<SendBriefNowResponse> {
  const url = `${API_BASE}/api/brief/send-now`;
  const res = await authedFetch(url, { method: 'POST' });
  return parseJson<SendBriefNowResponse>(res);
}

export async function fetchSayariUBO(entityId: string): Promise<SayariUBOResponse> {
  const url = `${API_BASE}/api/sayari/ubo`;
  const res = await authedFetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ entity_id: entityId }),
  });
  return parseJson<SayariUBOResponse>(res);
}

// --- COA Workspace ---

export async function fetchCOAs(status?: string): Promise<COA[]> {
  const params = status ? `?status=${status}` : '';
  const res = await authedFetch(`${API_BASE}/api/coa${params}`);
  return parseJson<COA[]>(res);
}

export async function fetchCOA(id: string): Promise<COA> {
  const res = await authedFetch(`${API_BASE}/api/coa/${id}`);
  return parseJson<COA>(res);
}

export async function createCOA(data: Partial<COA>): Promise<COA> {
  const res = await authedFetch(`${API_BASE}/api/coa`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  });
  return parseJson<COA>(res);
}

export async function updateCOA(id: string, data: Partial<COA>): Promise<COA> {
  const res = await authedFetch(`${API_BASE}/api/coa/${id}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  });
  return parseJson<COA>(res);
}

export async function deleteCOA(id: string): Promise<void> {
  const res = await authedFetch(`${API_BASE}/api/coa/${id}`, { method: 'DELETE' });
  await parseJson<{ status: string }>(res);
}

export async function generateCOAOptions(params: {
  analysis_data?: Record<string, unknown>;
  objective: string;
}): Promise<Partial<COA>[]> {
  const res = await authedFetch(`${API_BASE}/api/coa/generate`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(params),
  });
  return parseJson<Partial<COA>[]>(res);
}

// --- Risk Feed ---

export interface RiskFeedItem {
  id: string;
  category: 'company_sanctions' | 'people_sanctions' | 'markets' | string;
  severity: 'high' | 'medium' | 'low' | 'info' | string;
  headline: string;
  entity: string;
  source_url: string;
  /** Best-available date the underlying event happened
   *  (article publication / market session / sanction designation).
   *  null when no source date is available (e.g. raw OFAC SDN cards). */
  event_at: string | null;
  fetched_at: string;
  synthetic_payload: Record<string, unknown>;
}

export interface RiskFeedResponse {
  items: RiskFeedItem[];
  last_refresh: {
    at: string | null;
    source: string | null;
    count: number;
    errors: string[];
  };
  category_counts: Record<string, number>;
}

export async function fetchRiskFeed(): Promise<RiskFeedResponse> {
  const res = await authedFetch(`${API_BASE}/api/risk-feed`);
  return parseJson<RiskFeedResponse>(res);
}

export async function refreshRiskFeed(): Promise<RiskFeedResponse> {
  const res = await authedFetch(`${API_BASE}/api/risk-feed/refresh`, { method: 'POST' });
  return parseJson<RiskFeedResponse>(res);
}

export interface PreparedCoaPayload {
  item_id: string;
  synthetic_payload: Record<string, unknown>;
  sources_added: number;
}

export async function prepareCoaPayload(itemId: string): Promise<PreparedCoaPayload> {
  const res = await authedFetch(
    `${API_BASE}/api/risk-feed/${encodeURIComponent(itemId)}/prepare-coa`,
    { method: 'POST' },
  );
  return parseJson<PreparedCoaPayload>(res);
}

// --- Watchlist ---

export type WatchlistEntityKind = 'ticker' | 'gdelt_query' | 'gdelt_region' | 'sanctions_keyword';

export type WatchlistCategory = 'company_sanctions' | 'people_sanctions' | 'markets';

export interface WatchlistItem {
  id: string;
  username: string;
  label: string;
  query: string;
  entity_kind: WatchlistEntityKind;
  category: WatchlistCategory;
  active: boolean;
  created_at: string;
  updated_at: string;
}

export interface WatchlistResponse {
  items: WatchlistItem[];
  grouped: Record<WatchlistCategory, WatchlistItem[]>;
}

export interface WatchlistSuggestion {
  label: string;
  query: string;
  entity_kind: WatchlistEntityKind;
  category: WatchlistCategory;
}

export async function fetchWatchlist(): Promise<WatchlistResponse> {
  const res = await authedFetch(`${API_BASE}/api/watchlist`);
  return parseJson<WatchlistResponse>(res);
}

export async function fetchWatchlistSuggestions(): Promise<{ suggestions: WatchlistSuggestion[] }> {
  const res = await authedFetch(`${API_BASE}/api/watchlist/suggestions`);
  return parseJson<{ suggestions: WatchlistSuggestion[] }>(res);
}

export async function addWatchlistItem(payload: {
  label: string;
  query: string;
  entity_kind: WatchlistEntityKind;
  category: WatchlistCategory;
}): Promise<WatchlistItem> {
  const res = await authedFetch(`${API_BASE}/api/watchlist`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  return parseJson<WatchlistItem>(res);
}

export async function updateWatchlistItem(
  id: string,
  patch: { label?: string; query?: string; active?: boolean },
): Promise<WatchlistItem> {
  const res = await authedFetch(`${API_BASE}/api/watchlist/${encodeURIComponent(id)}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(patch),
  });
  return parseJson<WatchlistItem>(res);
}

export async function deleteWatchlistItem(id: string): Promise<void> {
  const res = await authedFetch(`${API_BASE}/api/watchlist/${encodeURIComponent(id)}`, {
    method: 'DELETE',
  });
  await parseJson<{ detail: string }>(res);
}

export interface WatchlistResolveSuggestion {
  label: string;
  query: string;
  entity_kind: WatchlistEntityKind;
  category: WatchlistCategory;
}

export interface WatchlistResolveEvidence {
  kind: 'yfinance' | 'ofac_sdn' | 'csl' | 'gdelt' | string;
  // Per-kind detail fields — left loose because shape varies.
  [k: string]: unknown;
}

export interface WatchlistResolveResponse {
  resolved: boolean;
  suggestion: WatchlistResolveSuggestion;
  evidence: WatchlistResolveEvidence[];
  confidence?: 'high' | 'medium' | 'low';
  hint?: string;
}

export async function resolveWatchlistEntity(name: string): Promise<WatchlistResolveResponse> {
  const res = await authedFetch(`${API_BASE}/api/watchlist/resolve`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name }),
  });
  return parseJson<WatchlistResolveResponse>(res);
}

// --- Notifications enrollment (admin-only) ---
//
// Admins use these endpoints to enroll users for SMS alerts and the weekly
// email digest. End-users have no UI to set their own phone/email.

export type EnrollmentRequest = {
  username: string;
  email: string | null;
  phone_number: string | null;
  sms_enabled: boolean;
  email_enabled: boolean;
  timezone: string;
};

export type Enrollment = EnrollmentRequest & {
  created_at: string | null;
  unsubscribed_at: string | null;
};

export async function listEnrollments(): Promise<Enrollment[]> {
  const res = await authedFetch(`${API_BASE}/api/admin/enrollments`);
  return parseJson<Enrollment[]>(res);
}

export async function enrollUser(payload: EnrollmentRequest): Promise<Enrollment> {
  const res = await authedFetch(`${API_BASE}/api/admin/enrollments`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  return parseJson<Enrollment>(res);
}

export async function unenrollUser(username: string): Promise<void> {
  const res = await authedFetch(
    `${API_BASE}/api/admin/enrollments/${encodeURIComponent(username)}`,
    { method: 'DELETE' },
  );
  if (!res.ok) {
    throw new Error(`Unenroll failed: ${res.status} ${res.statusText}`);
  }
}

export type TestSendResult = {
  status: string;
  provider_message_id: string | null;
  error: string | null;
};

export async function testSms(username: string): Promise<TestSendResult> {
  const res = await authedFetch(
    `${API_BASE}/api/admin/enrollments/${encodeURIComponent(username)}/test-sms`,
    { method: 'POST' },
  );
  if (!res.ok) {
    // Surface the JSON error detail when present (e.g. 404, 400)
    let detail = `${res.status} ${res.statusText}`;
    try {
      const body = await res.json();
      if (body?.detail) detail = body.detail;
    } catch {
      // ignore
    }
    throw new Error(detail);
  }
  return parseJson<TestSendResult>(res);
}

export async function testEmail(username: string): Promise<TestSendResult> {
  const res = await authedFetch(
    `${API_BASE}/api/admin/enrollments/${encodeURIComponent(username)}/test-email`,
    { method: 'POST' },
  );
  if (!res.ok) {
    let detail = `${res.status} ${res.statusText}`;
    try {
      const body = await res.json();
      if (body?.detail) detail = body.detail;
    } catch {
      // ignore
    }
    throw new Error(detail);
  }
  return parseJson<TestSendResult>(res);
}

// --- Monitoring ---

export async function fetchKPIs(): Promise<KPIData> {
  const res = await authedFetch(`${API_BASE}/api/monitoring/kpis`);
  return parseJson<KPIData>(res);
}

export async function fetchActivity(limit = 50): Promise<ActivityEntry[]> {
  const res = await authedFetch(`${API_BASE}/api/monitoring/activity?limit=${limit}`);
  return parseJson<ActivityEntry[]>(res);
}

export async function fetchMapData(): Promise<MapMarker[]> {
  const res = await authedFetch(`${API_BASE}/api/monitoring/map-data`);
  return parseJson<MapMarker[]>(res);
}

export async function fetchMacro(): Promise<MacroData> {
  const res = await authedFetch(`${API_BASE}/api/monitoring/macro`);
  return parseJson<MacroData>(res);
}

// --- Briefings ---

export async function fetchBriefings(): Promise<Briefing[]> {
  const res = await authedFetch(`${API_BASE}/api/briefing`);
  return parseJson<Briefing[]>(res);
}

export async function fetchBriefing(id: string): Promise<Briefing> {
  const res = await authedFetch(`${API_BASE}/api/briefing/${id}`);
  return parseJson<Briefing>(res);
}

export async function createBriefing(data: Partial<Briefing>): Promise<Briefing> {
  const res = await authedFetch(`${API_BASE}/api/briefing`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  });
  return parseJson<Briefing>(res);
}

export async function generateBriefing(params: {
  coa_id?: string;
  analysis_id?: string;
  briefing_type?: string;
}): Promise<Briefing> {
  const res = await authedFetch(`${API_BASE}/api/briefing/generate`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(params),
  });
  return parseJson<Briefing>(res);
}

export async function updateBriefing(id: string, data: Partial<Briefing>): Promise<Briefing> {
  const res = await authedFetch(`${API_BASE}/api/briefing/${id}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  });
  return parseJson<Briefing>(res);
}

export async function deleteBriefing(id: string): Promise<void> {
  const res = await authedFetch(`${API_BASE}/api/briefing/${id}`, { method: 'DELETE' });
  await parseJson<{ status: string }>(res);
}

// --- Auth ---

export async function login(
  username: string,
  password: string,
): Promise<{ access_token: string; username: string; is_admin?: boolean }> {
  const res = await fetch(`${API_BASE}/api/auth/login`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ username, password }),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: 'Login failed' }));
    throw new Error(err.detail || 'Login failed');
  }
  return res.json();
}

export async function fetchMe(): Promise<{ username: string; is_admin: boolean }> {
  const res = await authedFetch(`${API_BASE}/api/auth/me`);
  return parseJson<{ username: string; is_admin: boolean }>(res);
}

// --- Admin / Usage analytics ---

export interface UsageSummary {
  window_days: number;
  logins_per_day: { day: string; success: number; failure: number; unique_users: number }[];
  top_features: { feature: string; hits: number; unique_users: number }[];
  top_endpoints: {
    feature: string;
    method: string;
    path: string;
    hits: number;
    unique_users: number;
  }[];
  top_users: { username: string; events: number; last_seen: string }[];
  recent_logins: {
    timestamp: string;
    username: string;
    status_code: number;
    client_ip: string | null;
    detail: string | null;
  }[];
}

export async function fetchUsageSummary(days = 30): Promise<UsageSummary> {
  const res = await authedFetch(`${API_BASE}/api/admin/usage?days=${days}`);
  return parseJson<UsageSummary>(res);
}
