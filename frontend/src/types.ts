// --- Health ---

export interface HealthResponse {
  status: 'ok' | 'misconfigured';
  issues: string[];
  model: string;
  tools_available: boolean;
}

// --- Sanctions Impact ---

export interface CslMatch {
  name: string;
  source: string;
  programs: string[];
  start_date: string | null;
}

export interface SanctionsStatus {
  is_sanctioned: boolean;
  lists: string[];
  programs: string[];
  csl_matches: CslMatch[];
}

export interface TargetInfo {
  ticker: string;
  name: string;
  sector: string | null;
  industry: string | null;
  country: string | null;
  market_cap: number | null;
  current_price: number | null;
  change_pct: number | null;
  /**
   * True when neither Finnhub nor yfinance returned a live price for this
   * ticker (e.g. cloud-IP block, ticker not found, transient API failure).
   * UI should hide "$0.00" displays and show a "Live data temporarily
   * unavailable" notice instead of misleading the viewer with a real-looking
   * zero. Optional for backwards compatibility with older backend responses.
   */
  price_unavailable?: boolean;
  sanctions_status: SanctionsStatus;
}

export interface CurvePoint {
  day: number;
  pct: number;
}

export interface Comparable {
  name: string;
  ticker: string;
  sanction_date: string;
  description: string;
  sector: string;
  sanction_type?: string;
  color: string;
  curve: CurvePoint[];
}

export interface ProjectionPoint {
  day: number;
  pct: number;
  price: number;
}

export interface ProjectionSummaryData {
  pre_event_decline?: number;
  day_30_post?: number;
  day_30_range?: [number, number];
  day_60_post?: number;
  day_60_range?: [number, number];
  day_90_post?: number;
  day_90_range?: [number, number];
  max_drawdown?: number;
}

export interface Projection {
  mean: ProjectionPoint[];
  upper: ProjectionPoint[];
  lower: ProjectionPoint[];
  summary: ProjectionSummaryData;
  coherence_score?: number;
  coherence_low?: boolean;
}

export interface SanctionsImpactResponse {
  target: TargetInfo;
  comparables: Comparable[];
  projection: Projection;
  control_comparables?: Comparable[];
  control_projection?: Projection;
  metadata: {
    comparable_count: number;
    control_peer_count?: number;
    control_peer_tickers?: string[];
    time_window_days: [number, number];
    generated_at: string;
    sourcing_method?: 'claude' | 'cache' | 'static_fallback';
  };
  narrative?: string;
}

// --- Entity Graph ---

export interface GraphNode {
  id: string;
  label: string;
  title: string;
  group: string;
  color: string;
  sayariId?: string;
}

export interface GraphEdge {
  from: string;
  to: string;
  label: string;
  arrows: string;
  dashes: boolean;
}

export interface EntityGraphResponse {
  nodes: GraphNode[];
  edges: GraphEdge[];
  meta?: {
    query: string;
    node_count: number;
    edge_count: number;
  };
}

// --- Progress ---

export interface ProgressEntry {
  msg: string;
  type: 'step' | 'error' | 'done';
  time: string;
}

// --- Orchestrator ---

export interface OrchestratorFinding {
  category: string;
  finding: string;
  confidence: 'HIGH' | 'MEDIUM' | 'LOW';
  [key: string]: unknown;
}

export interface OrchestratorFriendlyFire {
  entity: string;
  details?: string;
  exposure_type?: string;
  estimated_impact?: string;
  [key: string]: unknown;
}

export interface OrchestratorEntity {
  id: string;
  name: string;
  entity_type: string;
  aliases?: string[];
  country?: string | null;
}

export interface OrchestratorRelationship {
  source_id: string;
  target_id: string;
  relationship_type: string;
}

export interface ImpactAssessmentResult {
  query: { raw_query: string; scenario_type: string };
  scenario_type: string;
  executive_summary: string;
  findings: OrchestratorFinding[];
  friendly_fire: OrchestratorFriendlyFire[];
  confidence_summary: Record<string, string>;
  sources: { name: string; url?: string | null; accessed_at?: string }[];
  recommendations: string[];
  /** Raw tool results from every pipeline step — available to follow-up chat */
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  tool_results?: Record<string, any>;
  /** Present when the orchestrator extracted a graph */
  entity_graph?: {
    entities: OrchestratorEntity[];
    relationships: OrchestratorRelationship[];
  };
}

// --- Orchestrator swarm events (Phase 3 contract) ---

/** Tool domains used for the agent colour badges. `news` is P2. */
export type OrchestratorDomain =
  | 'sanctions'
  | 'corporate'
  | 'market'
  | 'trade'
  | 'geopolitical'
  | 'economic'
  | 'sayari'
  | 'news'
  | 'unknown';

/** Pipeline stage transition. */
export interface OrchestratorPhaseEvent {
  type: 'phase';
  name: 'decompose' | 'execute' | 'synthesize' | 'complete';
  status: 'start' | 'done';
}

export interface OrchestratorPlanStep {
  step: number;
  description: string;
  tools: string[];
}

/** The decomposition — emitted once after decompose. */
export interface OrchestratorPlanEvent {
  type: 'plan';
  steps: OrchestratorPlanStep[];
}

/** One `running` then one `done|error` per tool call. Correlate by (name, step). */
export interface OrchestratorToolEvent {
  type: 'tool';
  step: number;
  name: string;
  domain: OrchestratorDomain;
  /** Present on the `running` event. */
  task?: string;
  status: 'running' | 'done' | 'error';
  /** Chip text on done/error, e.g. "6 results, medium" / "error". */
  summary?: string;
  /** Tool duration in ms (on done/error). */
  ms?: number;
  /** Real findings for the live feed (on done/error): top rows + sources. */
  detail?: OrchestratorAgentDetail;
}

/** Each agent's actual findings — uniform across all tools — for the live feed. */
export interface OrchestratorAgentDetail {
  /** Up to 5 labelled rows (headlines, sanctions matches, owners, ...). */
  items: string[];
  confidence?: string;
  sources?: string[];
  error?: string;
}

/** Streamed executive-summary text during synthesis — latest event wins. */
export interface OrchestratorSynthesisEvent {
  type: 'synthesis';
  text: string;
}

export type OrchestratorEvent =
  | OrchestratorPhaseEvent
  | OrchestratorPlanEvent
  | OrchestratorToolEvent
  | OrchestratorSynthesisEvent;

export interface OrchestratorStatusResponse {
  analysis_id: string;
  status: 'running' | 'completed' | 'failed';
  progress: string[];
  /** Append-only swarm event stream; full list returned each poll. */
  events?: OrchestratorEvent[];
  result: ImpactAssessmentResult | null;
  error: string | null;
}

// --- Self-serve briefs (Phase 3 contract) ---

export interface SubscribeResponse {
  status: string;
  username: string;
  email: string;
}

export interface SendBriefNowResponse {
  status: 'sent' | 'failed' | 'skipped_kill_switch' | 'skipped_disabled' | 'skipped_allowlist';
  email: string | null;
  error: string | null;
  provider_message_id?: string | null;
}

export interface StartAnalysisResponse {
  analysis_id: string;
  status: string;
}

// "Did you mean…?" near-match: the nearest fast-replay query for a close-but-not-
// exact question, or null. Only ever a suggestion the user confirms — never an
// auto-answer. Mirrors backend SuggestResponse in src/routers/orchestrator.py.
export interface SuggestResponse {
  suggestion: string | null;
  score: number;
}

// --- Entity Resolution ---

export interface EntityResolutionResponse {
  entity_type: 'company' | 'person' | 'sector' | 'vessel';
  entity_name: string;
  confidence: number;
  reasoning: string;
}

// --- Person Profile ---

export interface PersonAffiliation {
  company: string;
  role: string;
  nationality: string;
  active: boolean;
}

export interface OffshoreConnection {
  entity: string;
  dataset: string;
  jurisdiction: string;
}

export interface RecentEvent {
  title: string;
  date: string;
  source: string;
  tone: number | null;
}

export type RiskSeverity = 'none' | 'suggested' | 'expected' | 'discouraged' | 'prohibited';

export interface RiskFactorEvidence {
  type: string;
  description: string;
  source: string;
  date?: string | null;
  tone?: number | null;
  active?: boolean | null;
  jurisdiction?: string | null;
  programs?: string[];
}

export interface RiskFactor {
  title: string;
  severity: RiskSeverity;
  score: number;
  summary: string;
  evidence: RiskFactorEvidence[];
}

export interface PersonCandidate {
  name: string;
  sources: string[]; // e.g. ["opensanctions", "opencorporates"]
  sanctioned: boolean;
  sanction_programs: string[];
  primary_affiliation: string | null;
  country: string | null;
  score: number; // 0-1 ranking signal from backend
}

export interface PersonNetworkNode {
  id: string;
  label: string;
  group: 'person' | 'company';
  depth: number; // 0 = central, 1 = L1, 2 = L2
  sanctioned: boolean;
}

export interface PersonNetworkEdge {
  from: string;
  to: string;
  label?: string | null;
}

export interface PersonNetworkResponse {
  central: string;
  depth: number;
  nodes: PersonNetworkNode[];
  edges: PersonNetworkEdge[];
}

export interface PersonProfileResponse {
  name: string;
  is_sanctioned: boolean;
  sanction_programs: string[];
  aliases: string[];
  nationality: string | null;
  dob: string | null;
  affiliations: PersonAffiliation[];
  offshore_connections: OffshoreConnection[];
  recent_events: RecentEvent[];
  graph: { nodes: GraphNode[]; edges: GraphEdge[] };
  narrative?: string;
  sources: string[];
  // Added in person-search-v1: structured factor cards.
  // Optional so older backends can still be consumed without crashing.
  risk_factors?: RiskFactor[];
}

// --- Sector Analysis ---

export interface CompanyProfile {
  name: string;
  ticker: string | null;
  country: string | null;
  is_sanctioned: boolean;
  sanction_names: string[];
}

export interface SupplyChainExposure {
  label: string;
  commodity_code: string;
  import_share_pct: number;
  top_suppliers: unknown[];
}

export interface GeopoliticalTension {
  pair: string;
  event_count: number;
  tension_level: string;
  avg_tone: number | null;
}

export interface SectorAnalysisResponse {
  sector: string;
  sector_key: string;
  company_count: number;
  sanctioned_count: number;
  companies: CompanyProfile[];
  graph: { nodes: GraphNode[]; edges: GraphEdge[] };
  narrative?: string;
  supply_chain_exposures?: SupplyChainExposure[];
  geopolitical_tensions?: GeopoliticalTension[];
  // Sources may be plain strings (legacy) or {name, url, description} dicts
  // (current backend shape). Renderer must handle both — see SectorView.
  sources: (string | { name: string; url?: string; description?: string })[];
}

// --- Sayari ---

export interface SayariEntity {
  entity_id: string;
  label: string;
  type: string;
  country: string | null;
  addresses: string[];
  identifiers: string[];
  sources: string[];
  pep: boolean;
  sanctioned: boolean;
}

export interface SayariRelationship {
  source_id: string;
  target_id: string;
  relationship_type: string;
  attributes: Record<string, string>;
}

export interface SayariResolveResponse {
  entities: SayariEntity[];
  query: string;
}

export interface SayariTraversalResponse {
  root_id: string;
  entities: SayariEntity[];
  relationships: SayariRelationship[];
}

export interface SayariUBOOwner {
  entity_id: string;
  name: string;
  type: string;
  country: string | null;
  ownership_percentage: number | null;
  path_length: number;
  sanctioned: boolean;
  pep: boolean;
}

export interface SayariUBOResponse {
  target_id: string;
  target_name: string;
  owners: SayariUBOOwner[];
}

// --- Entity Risk Report ---

export interface RiskIndicator {
  label: string;
  value: string;
  severity: 'high' | 'medium' | 'low';
}

export interface SanctionDetail {
  name: string;
  score: number;
  programs: string[];
  remarks: string | null;
}

export interface EntityRiskReport {
  name: string;
  entity_type: string;
  risk_level: 'HIGH' | 'MEDIUM' | 'LOW';
  is_sanctioned: boolean;
  sanction_programs: string[];
  sanction_lists: string[];
  sanction_details: SanctionDetail[];
  country: string | null;
  corporate_info: {
    legal_name?: string;
    lei?: string;
    country?: string;
    status?: string;
    ultimate_parent_lei?: string;
    incorporation_date?: string;
    registered_address?: string;
  };
  officers: { name: string; role: string }[];
  offshore_flags: { entity: string; dataset: string; jurisdiction: string }[];
  market_info: {
    ticker: string;
    current_price: number | null;
    market_cap: number | null;
    change_pct: number | null;
    sector: string | null;
    industry: string | null;
    exchange: string | null;
    fifty_two_week_high: number | null;
    fifty_two_week_low: number | null;
    pct_from_52w_high: number | null;
    analyst_target: number | null;
    analyst_recommendation: string | null;
    analyst_count: number | null;
    description: string | null;
  } | null;
  exposure: {
    top_holders: {
      name: string;
      pct_held: number | null;
      value_usd: number | null;
      is_pension: boolean;
    }[];
    pension_count: number;
    pension_names: string[];
    total_institutional_usd: number | null;
  } | null;
  risk_indicators: RiskIndicator[];
  narrative: string;
  sources: string[];
  generated_at: string;
}

// --- Sanctions Screening ---

export interface SanctionsScreenResult {
  sanctioned: boolean;
  lists: string[];
  programs: string[];
}

export interface SanctionsScreenBatchResponse {
  results: Record<string, SanctionsScreenResult>;
}

// --- Vessel Track ---

export interface VesselDetail {
  name: string;
  imo: string;
  mmsi: string;
  callsign: string;
  flag: string;
  vessel_type: string;
  length: number | null;
  width: number | null;
  deadweight: number;
  latitude: number;
  longitude: number;
  speed: number;
  course: number;
  heading: number;
  status: string;
  destination: string;
  eta: string;
  last_position_epoch: number;
  source: string;
  note?: string;
  owner?: string;
}

export interface RoutePoint {
  lat: number;
  lon: number;
  speed: number;
  ts: number;
}

export interface SanctionsMatch {
  name: string;
  score: number;
  programs: string[];
}

export interface OwnershipLink {
  entity_id: string;
  name: string;
  entity_type: string;
  country: string | null;
  ownership_percentage: number | null;
  is_sanctioned: boolean;
  is_pep: boolean;
  depth: number;
  relationship_type: string;
  parent_entity_id: string | null;
}

export interface SankeyFlow {
  from: string;
  to: string;
  flow: number;
}

export interface TradeRecord {
  supplier: string;
  buyer: string;
  supplier_risks: string[];
  buyer_risks: string[];
  hs_code: string | null;
  hs_description: string | null;
  commodity_category: string | null;
  departure_country: string | null;
  arrival_country: string | null;
  date: string | null;
  weight_kg: number | null;
  value_usd: number | null;
}

export interface TradeActivity {
  records: TradeRecord[];
  top_hs_codes: { code: string; description: string }[];
  trade_countries: string[];
  record_count: number;
  sankey_flows: SankeyFlow[];
}

export interface PortCall {
  port_name: string;
  country: string;
  latitude: number;
  longitude: number;
  arrival: string;
  departure: string;
}

export interface PortStopInferred {
  latitude: number;
  longitude: number;
  arrival_ts: number;
  departure_ts: number;
  duration_hours: number;
  position_count: number;
}

export interface VesselTrackResponse {
  vessel: VesselDetail;
  is_sanctioned: boolean;
  sanctions_matches: SanctionsMatch[];
  route_history: RoutePoint[];
  countries_visited: string[];
  port_calls: PortCall[];
  port_stops_inferred: PortStopInferred[];
  ownership_chain: OwnershipLink[];
  owner_name: string | null;
  trade_activity: TradeActivity | null;
  risk_scores: Record<string, number>;
  graph: { nodes: GraphNode[]; edges: GraphEdge[] };
  trade_graph: { nodes: GraphNode[]; edges: GraphEdge[] };
  narrative?: string;
  recommendations?: string[];
  // Sources may be plain strings (legacy) or {name, url, description} dicts
  // (current backend shape). Renderer must handle both — see VesselView.
  sources: (string | { name: string; url?: string; record_url?: string; description?: string })[];
}

// --- Shared utility types ---

export type EntityType = 'company' | 'person' | 'sector' | 'vessel';

export interface EntityResolution {
  entity_type: EntityType;
  entity_name: string;
  confidence: number;
  reasoning: string;
}

export type AnalysisResult =
  | { type: 'company'; data: SanctionsImpactResponse }
  | { type: 'person'; data: PersonProfileResponse }
  | { type: 'vessel'; data: VesselTrackResponse }
  | { type: 'sector'; data: SectorAnalysisResponse };

// --- COA Workspace ---

export interface BriefingSource {
  name: string;
  url?: string;
  record_url?: string;
  accessed_at?: string;
  description?: string;
}

export interface COA {
  id: string;
  name: string;
  description: string;
  target_entities: string[];
  action_type: string;
  status: 'draft' | 'under_review' | 'approved' | 'executing' | 'assessed';
  confidence: number | null;
  source_analysis_id: string | null;
  recommendations: string[];
  friendly_fire: Record<string, unknown>[];
  expected_effects: string[];
  sources?: BriefingSource[];
  // Analyst-grade "why" — explains the case for choosing this COA with
  // [N] citation markers that map to entries in `sources`. Optional for
  // backwards compatibility with COAs created before this field existed.
  rationale?: string;
  created_at: string;
  updated_at: string;
}

// --- Monitoring ---

export interface KPIData {
  active_coas: number;
  total_coas: number;
  total_activity: number;
  last_event: string | null;
  total_briefings: number;
}

export interface ActivityEntry {
  id: number;
  timestamp: string;
  event_type: string;
  source: string;
  message: string;
  severity: string;
  related_id: string | null;
}

export interface MapMarker {
  lat: number;
  lon: number;
  label: string;
  type: string;
  status: string;
}

export interface MacroData {
  usd_cny: number | null;
  brent: number | null;
  vix: number | null;
  dxy: number | null;
  sparklines: {
    capital_flight: number[];
    currency_vol: number[];
    equity_swap: number[];
  };
}

// --- Briefings ---

export interface Briefing {
  id: string;
  title: string;
  type: 'coa_brief' | 'bda_report' | 'situation_update' | 'exercise_summary';
  status: 'draft' | 'reviewing' | 'finalized';
  reference_id: string | null;
  content_markdown: string;
  sources?: BriefingSource[];
  created_at: string;
  updated_at: string;
}
