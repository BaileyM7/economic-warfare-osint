# Handoff — Economic Warfare OSINT

This document gives a new Claude Code session everything needed to pick up the implementation plan from where the previous session left off. Read this first, then `DEMO_PLAN.md` for the full plan, then `ARCHITECTURE.md` for code-level detail.

---

## Environment

**Repo:** `C:\Users\nitin\projects\economic-warfare\economic-warfare-osint`
**Branch:** `demo_stock_price_4` (active) — `master` exists but is behind
**Python env:** conda `econ312` — always activate before running anything

```bash
conda activate econ312
```

**Run the server:**
```bash
conda activate econ312
cd C:\Users\nitin\projects\economic-warfare\economic-warfare-osint
uv run uvicorn src.api:app --reload --host 0.0.0.0 --port 8000
```

**CLI analysis:**
```bash
conda activate econ312
cd C:\Users\nitin\projects\economic-warfare\economic-warfare-osint
uv run python -m src.orchestrator.main "your question here"
```

**Run tests:**
```bash
conda activate econ312
uv run --extra dev pytest
```

**API keys** are already set in `.env` (do not regenerate or commit):
- `ANTHROPIC_API_KEY` — set, required
- `FRED_API_KEY` — set
- `COMTRADE_API_KEY` — set
- `ACLED_API_KEY` + `ACLED_EMAIL` + `ACLED_PASSWORD` + `REFRESH_TOKEN` — set
- `DATALASTIC_API_KEY` — **empty** (this is a gap — see vessel work below)
- `OPENCORPORATES_API_KEY` — empty (works without it, just rate-limited)

**Frontend:** There are two frontends — understand the distinction before touching anything:

1. **React app (submodule)** — `frontend/` is a git submodule (`https://github.com/deveshkumars/economicgamingv1.git`, branch `demo_stock_price_3`). This is a proper **React 18 + TypeScript + Vite** app. It runs on its own dev server (port 5173) and proxies `/api/*` to the FastAPI backend at port 8000 via `vite.config.ts`. This is the right place to build new frontend features.

2. **Inline HTML fallback** — `_read_index_html()` in `src/api.py` (~line 796) serves a self-contained HTML/CSS/JS string at `GET /`. This is what worked before the submodule was checked out. It has feature parity with the React app (sanctions impact only).

**The system worked before the submodule** because the inline HTML served directly. The React app is a cleaner development target but requires its own build step to deploy via FastAPI.

**Dev workflow with the React frontend:**
```bash
# Terminal 1 — backend
conda activate econ312
cd C:\Users\nitin\projects\economic-warfare\economic-warfare-osint
uv run uvicorn src.api:app --reload --host 0.0.0.0 --port 8000

# Terminal 2 — frontend dev server
cd C:\Users\nitin\projects\economic-warfare\economic-warfare-osint\frontend
npm install   # first time only
npm run dev   # starts on http://localhost:5173, proxies /api to :8000
```

**For production / single-server deployment:**
```bash
cd frontend && npm run build
# then serve frontend/dist/ as static files from FastAPI, or keep using the inline HTML fallback
```

---

## What This System Does

Multi-agent OSINT system for economic warfare exercise support. Analysts ask natural-language questions; the system gathers data from 15+ free sources and returns structured impact assessments.

**Four query classes, four endpoints:**

| Query type | Endpoint | Input |
|-----------|----------|-------|
| Company / sanctions impact | `POST /api/sanctions-impact` | Any ticker or company name |
| Person risk profile | `POST /api/person-profile` | Any named individual |
| Sector risk analysis | `POST /api/sector-analysis` | Any industry/sector string |
| Vessel intelligence | `POST /api/vessel-track` | Vessel name, 7-digit IMO, or 9-digit MMSI |

There is also `POST /api/analyze` which runs the full LLM orchestrator pipeline for open-ended compound queries. `POST /api/entity-graph` and `POST /api/resolve-entity` are supporting endpoints.

**Architecture (three layers):**
1. `src/orchestrator/` — Claude decomposes questions → dispatches tools → synthesizes `ImpactAssessment`
2. `src/tools/{sanctions,corporate,market,trade,geopolitical,economic}/` — 30 async tool functions returning `ToolResponse`
3. `src/fusion/` — extracts `EntityGraph`, renders markdown + vis.js

The canonical data model is `src/common/types.py`. Every tool returns a `ToolResponse(data, confidence, sources, errors)`.

---

## Current State — What Works and What Doesn't

### Working (real API calls, real data)
- All 6 tool domains: sanctions (OpenSanctions + OFAC), corporate (OpenCorporates + GLEIF + ICIJ), market (yfinance + SEC EDGAR + FRED), trade (UN Comtrade + UNCTADstat), geopolitical (GDELT + ACLED), economic (FRED + IMF + World Bank)
- `/api/sanctions-impact` — end-to-end: yfinance stock data + OFAC check + comparable price curves + chart projection
- `/api/person-profile` — end-to-end: OpenSanctions + OFAC + OpenCorporates officers + ICIJ + GDELT; returns full JSON including vis.js graph
- `/api/sector-analysis` — works for 5 pre-defined sectors only; runs OFAC checks per company in parallel
- `/api/vessel-track` — OFAC check on vessel name is real; AIS lookup (Datalastic) **falls back to mock data** because `DATALASTIC_API_KEY` is empty
- `/api/entity-graph` — GLEIF corporate tree + OFAC sanctions network + sector peers; real data
- `/api/resolve-entity` — Claude classifies query as company/person/sector/vessel; works

### Not working / gaps
1. **Frontend only renders the sanctions impact chart.** The person, sector, and vessel endpoints return real JSON but nothing in the frontend displays it. The frontend is the inline HTML string in `_read_index_html()` in `src/api.py`.

2. **Sector coverage is hardcoded to 5 sectors** in `_SECTOR_COMPANIES` dict in `src/api.py` (lines ~655–696): semiconductor, energy, shipping, rare earth, telecom. Any other sector silently returns semiconductor as a fallback (via `_match_sector()`). This breaks on any real query outside that list.

3. **Comparables dataset is narrow** — `SANCTIONS_COMPARABLES` in `src/sanctions_impact.py` contains **11 entries** (ZTE, Alibaba, Xiaomi, Full Truck Alliance, Tencent Music, Bilibili, NIO, PDD, Baidu, Micron, KWEB), all Chinese-listed or US-listed Chinese ADRs. A `SECTOR_GROUPS` dict already exists for sector-based filtering. Fix: add a `sanction_type` field to each entry and append new cases (Nvidia, ASML, Gazprom, etc.) — do not restructure the data format.

4. **Vessel AIS is always mock** — `src/tools/vessels/client.py` checks for `DATALASTIC_API_KEY` and if absent returns `_mock_vessel_list()` / `_mock_vessel_detail()` / `_mock_vessel_history()` with a `"note": "Demo mode"` field. There is no `server.py` in `src/tools/vessels/` — the vessel functions are called directly from `src/api.py`.

5. **No LLM narratives** — all 4 endpoints return structured data only. A clean person profile looks like an empty result rather than "no derogatory findings across X sources." The narrative layer is what makes these look like analyst outputs rather than raw data dumps.

6. **`/api/analyze` (full orchestrator)** — the code at `src/orchestrator/main.py` is implemented and the endpoint exists in `src/api.py` (lines 137–187). There is a comment at line 788 "commented out for demo" but inspecting the actual code, `_run_analysis()` is defined (lines 137–148) and `start_analysis()` calls `asyncio.create_task(_run_analysis(...))`. It is not actually commented out. **Needs smoke testing to confirm it runs end-to-end.**

---

## The Work — Priority Order

This is a direct extract from `DEMO_PLAN.md`. Execute in this order.

### Step 0 — Smoke test (do this first, ~2h)
Before writing any new code, validate that the existing endpoints actually return real data:

```bash
conda activate econ312
# In one terminal:
uv run uvicorn src.api:app --reload --host 0.0.0.0 --port 8000

# In another terminal, hit each endpoint:
curl -s -X POST http://localhost:8000/api/sanctions-impact \
  -H "Content-Type: application/json" -d '{"ticker":"SMCI"}' | python -m json.tool

curl -s -X POST http://localhost:8000/api/person-profile \
  -H "Content-Type: application/json" -d '{"name":"Viktor Vekselberg"}' | python -m json.tool

curl -s -X POST http://localhost:8000/api/sector-analysis \
  -H "Content-Type: application/json" -d '{"sector":"semiconductor"}' | python -m json.tool

curl -s -X POST http://localhost:8000/api/vessel-track \
  -H "Content-Type: application/json" -d '{"query":"Lana"}' | python -m json.tool

curl -s -X POST http://localhost:8000/api/analyze/sync \
  -H "Content-Type: application/json" -d '{"query":"What happens if we sanction Huawei?"}' | python -m json.tool
```

Record what each returns. Note which fields are populated vs. empty. Write findings to `SMOKE_TEST.md`.

---

### Step 1 — Expand comparables with `sanction_type` field (~3h)
**File:** `src/sanctions_impact.py`

Do **not** restructure `SANCTIONS_COMPARABLES` from a flat list — just add a `"sanction_type"` key to each existing dict entry and append new entries. The existing `SECTOR_GROUPS` dict already handles sector-based filtering; add `sanction_type` filtering alongside it using the same ≥3-match fallback logic.

Tag existing 11 entries:
- ZTE, Alibaba, Xiaomi, FTA, Tencent Music, Bilibili, NIO, PDD, Baidu, KWEB → `"ofac_ccmc"`
- Micron → `"retaliation"`

New entries to append (verify ticker availability in yfinance before adding):
- Nvidia `NVDA` 2022-10-07 → `"us_export_control"` (BIS advanced chip rule)
- Applied Materials `AMAT` 2022-10-07 → `"us_export_control"`
- ASML `ASML` 2023-01-28 → `"us_export_control"` (Dutch EUV license revoked)
- Qualcomm `QCOM` 2019-05-15 → `"us_export_control"` (Huawei supply ban)
- Seagate `STX` 2023-04-19 → `"bis_penalty"` (BIS fine for Huawei sales)
- Gazprom ADR `OGZPY` 2022-02-24 → `"sectoral"` (EU/US energy sanctions)
- Sberbank ADR `SBRCY` 2022-02-24 → `"swift_cutoff"` (SWIFT exclusion)

Update `get_comparable_curves()` to accept an optional `sanction_type` parameter and filter by it when provided, falling back to full set if fewer than 3 matches. Update `run_sanctions_impact()` to infer `sanction_type` from the sanctions context returned by `get_sanctions_context()`.

---

### Step 2 — Expand sector registry (~3h)
**File:** `src/api.py` — `_SECTOR_COMPANIES` dict (lines ~655–696) and `_match_sector()` function (lines ~699–711)

Add these sector keys with representative companies (use real tickers where available):

```python
"defense_aerospace": [
    {"name": "Lockheed Martin", "ticker": "LMT", "country": "US"},
    {"name": "RTX (Raytheon)", "ticker": "RTX", "country": "US"},
    {"name": "Northrop Grumman", "ticker": "NOC", "country": "US"},
    {"name": "L3Harris Technologies", "ticker": "LHX", "country": "US"},
    {"name": "BAE Systems", "ticker": "BAESY", "country": "GB"},
    {"name": "Leonardo", "ticker": "FINMY", "country": "IT"},
    {"name": "Thales", "ticker": "THLEF", "country": "FR"},
    {"name": "AVIC (private)", "ticker": None, "country": "CN"},
],
"aircraft_mro": [
    {"name": "AAR Corp", "ticker": "AIR", "country": "US"},
    {"name": "Heico Corporation", "ticker": "HEI", "country": "US"},
    {"name": "TransDigm Group", "ticker": "TDG", "country": "US"},
    {"name": "StandardAero (private)", "ticker": None, "country": "US"},
    {"name": "Lufthansa Technik (private)", "ticker": None, "country": "DE"},
    {"name": "ST Engineering", "ticker": "S63.SI", "country": "SG"},
    {"name": "HAECO", "ticker": "0044.HK", "country": "HK"},
    {"name": "VSMPO-AVISMA (titanium supplier)", "ticker": None, "country": "RU"},
],
"critical_minerals": [
    {"name": "MP Materials", "ticker": "MP", "country": "US"},
    {"name": "Lynas Rare Earths", "ticker": "LYC.AX", "country": "AU"},
    {"name": "Albemarle", "ticker": "ALB", "country": "US"},
    {"name": "Ganfeng Lithium", "ticker": "1772.HK", "country": "CN"},
    {"name": "China Northern Rare Earth", "ticker": "600111.SS", "country": "CN"},
    {"name": "Pilbara Minerals", "ticker": "PLS.AX", "country": "AU"},
],
"dual_use_tech": [
    {"name": "DJI (private)", "ticker": None, "country": "CN"},
    {"name": "Hikvision", "ticker": "002415.SZ", "country": "CN"},
    {"name": "Dahua Technology", "ticker": "002236.SZ", "country": "CN"},
    {"name": "SenseTime", "ticker": "0020.HK", "country": "CN"},
    {"name": "Megvii (private)", "ticker": None, "country": "CN"},
],
"port_logistics": [
    {"name": "COSCO Shipping Ports", "ticker": "1199.HK", "country": "CN"},
    {"name": "Hutchison Ports (private)", "ticker": None, "country": "HK"},
    {"name": "DP World (private)", "ticker": None, "country": "AE"},
    {"name": "PSA International (private)", "ticker": None, "country": "SG"},
    {"name": "ICTSI", "ticker": "ICT.PS", "country": "PH"},
],
"financial": [
    {"name": "Sberbank", "ticker": "SBRCY", "country": "RU"},
    {"name": "VTB Bank", "ticker": "VTBR.ME", "country": "RU"},
    {"name": "Bank of China", "ticker": "3988.HK", "country": "CN"},
    {"name": "HSBC", "ticker": "HSBC", "country": "GB"},
    {"name": "Standard Chartered", "ticker": "SCBFF", "country": "GB"},
],
"space_satellite": [
    {"name": "Planet Labs", "ticker": "PL", "country": "US"},
    {"name": "Maxar Technologies (private)", "ticker": None, "country": "US"},
    {"name": "Iridium", "ticker": "IRDM", "country": "US"},
    {"name": "Spire Global", "ticker": "SPIR", "country": "US"},
    {"name": "CASC (private)", "ticker": None, "country": "CN"},
],
```

Replace the hardcoded fuzzy `_match_sector()` with a function that:
1. Checks for exact or substring match against registry keys + a `_SECTOR_ALIASES` dict
2. If no match, makes a single lightweight Claude call to identify the closest sector key (or `"unknown"`)
3. If `"unknown"`, dynamically asks Claude for the top 8 publicly-traded companies in the sector and their tickers — construct a temporary company list on the fly

Also wire the `sector_analysis` endpoint to additionally call trade and geopolitical tools for aviation/MRO queries:
- `get_supply_chain_exposure(country, commodity_code)` for key input commodities (titanium HS 810890, carbon fiber HS 681510, rare earth magnets HS 850511)
- `get_bilateral_tensions(country1, country2, days=180)` for key bilateral pairs (US-China, US-Russia)

---

### Step 3 — Fix vessel data source (~3h)
**File:** `src/tools/vessels/client.py`

The mock fallback needs to be replaced with real data. There is no `server.py` in `src/tools/vessels/` — the vessel functions are imported directly into `src/api.py` so you do not need to create a server.py unless adding to the tool registry.

Add a new function `vessel_find_opensanctions(name: str) -> list[dict]` that:
1. Calls `https://api.opensanctions.org/entities/_search?q={name}&schema=Vessel` (no API key required)
2. Parses the response: extract `id`, `caption`, properties for `imoNumber`, `mmsi`, `flag`, `owner`, `pastOwners`, `sanctionedVessel`
3. For each result, pull associated `Organization` entities (the owner/operator) by following the `owner` relationship
4. Returns a list of dicts in the same shape as the Datalastic response so `vessel_track` endpoint doesn't need changes

Update `vessel_find()`, `vessel_by_imo()`, `vessel_by_mmsi()` to call `vessel_find_opensanctions()` as primary source when Datalastic key is absent, instead of calling `_mock_vessel_list()`.

Add a `data/fixtures/vessels.json` file with 5–6 curated vessel objects for non-sanctioned vessel lookups (use real IMO numbers from public AIS sources — e.g., a container ship, a bulk carrier, a product tanker). Shape:
```json
[
  {"name": "...", "imo": "...", "mmsi": "...", "flag": "...", "vessel_type": "...",
   "deadweight": 0, "latitude": 0.0, "longitude": 0.0, "speed": 0.0,
   "status": "...", "destination": "...", "last_position_epoch": 0}
]
```

This replaces the current mock with a fixture fallback that at least uses real vessel identifiers.

---

### Step 4 — Multi-mode frontend (~10h)
**Target:** `frontend/src/` — the React + TypeScript app in the submodule

This is the largest piece of work. The React app currently only handles the sanctions impact flow. It needs to support all four query types with automatic mode detection.

**Current React app structure (understand before editing):**
```
frontend/src/
  App.tsx                        ← top-level state, orchestrates views — EDIT THIS
  api.ts                         ← all fetch calls to backend — ADD new fetch functions here
  types.ts                       ← TypeScript interfaces for API responses — ADD new types here
  components/
    QueryBox.tsx                 ← search bar + example chips — EDIT to support all 4 modes
    ImpactChart.tsx              ← Chart.js price chart (company view, keep as-is)
    ComparablesTable.tsx         ← comparables table (company view, keep as-is)
    ImpactInfoCards.tsx          ← target info cards (company view, keep as-is)
    ProjectionSummary.tsx        ← 30/60/90d summary (company view, keep as-is)
    EntityGraphSection.tsx       ← vis.js graph — currently commented out, reuse for all views
    GraphViewer.tsx              ← vis.js Network wrapper
    ProgressPanel.tsx            ← progress log (reuse for orchestrator streaming)
    Header.tsx                   ← top bar
```

**What's already done in the React app:**
- `EntityGraphSection` and `GraphViewer` are implemented but commented out in `App.tsx` (lines 10, 150) — uncomment and reuse for all four views
- `fetchEntityGraph` is already in `api.ts` (line 39) — just needs uncommenting in App.tsx
- `vis-network` is already a dependency in `package.json`
- `GraphNode` and `GraphEdge` types are already in `types.ts`

**What needs to be added:**

`api.ts` — add fetch functions:
```typescript
export async function resolveEntity(query: string): Promise<EntityResolutionResponse>
export async function fetchPersonProfile(name: string): Promise<PersonProfileResponse>
export async function fetchSectorAnalysis(sector: string): Promise<SectorAnalysisResponse>
export async function fetchVesselTrack(query: string): Promise<VesselTrackResponse>
```

`types.ts` — add interfaces for the 3 new response types. Shape them from the actual API responses (run smoke tests in Step 0 to get real response JSON to type against):
- `PersonProfileResponse` — name, is_sanctioned, sanction_programs, aliases, nationality, affiliations[], offshore_connections[], recent_events[], graph{nodes,edges}
- `SectorAnalysisResponse` — sector, company_count, sanctioned_count, companies[], graph{nodes,edges}
- `VesselTrackResponse` — vessel{}, is_sanctioned, sanctions_matches[], route_history[], graph{nodes,edges}
- `EntityResolutionResponse` — entity_type, entity_name, confidence, reasoning

`App.tsx` — restructure around two-tier mode detection:
1. On submit, run client-side classification first (instant):
   - `/^\d{9}$/` → vessel (MMSI); `/^\d{7}$/` or `/^imo\s*\d/i` → vessel (IMO)
   - known sector keywords (mro, semiconductor, aviation, etc.) → sector
   - otherwise → ambiguous
2. For ambiguous queries only, call `resolveEntity(query)` to get `entity_type`
3. Route to the appropriate fetch + view; keep existing company flow unchanged

`QueryBox.tsx` — update:
- Change placeholder to `"Search any company, person, sector, or vessel..."`
- Update `KNOWN_MAP` / `extractTicker` — currently hardcoded ticker extraction won't work for persons/sectors/vessels. For non-company queries, pass the raw query through instead of trying to extract a ticker.
- Update example chips:
```typescript
const EXAMPLES = [
  { label: 'Supermicro (SMCI)', query: 'SMCI' },
  { label: 'Viktor Vekselberg', query: 'Viktor Vekselberg' },
  { label: 'Aircraft MRO sector', query: 'Aircraft MRO' },
  { label: 'Vessel: Lana', query: 'Lana' },
  { label: 'Huawei', query: 'Huawei' },
  { label: 'Semiconductor sector', query: 'Semiconductor' },
]
```

**New view components to create:**

`PersonView.tsx` — renders `PersonProfileResponse`:
- Profile header: name, sanctions status badge, aliases, nationality
- Affiliations table: company | role | active badge
- Offshore connections (only if non-empty)
- Recent events list: title, date, tone badge
- `EntityGraphSection` with graph data

`SectorView.tsx` — renders `SectorAnalysisResponse`:
- Summary: X companies, Y sanctioned
- Company table: name | country | ticker | sanctions badge
- `EntityGraphSection` with graph data

`VesselView.tsx` — renders `VesselTrackResponse`:
- Vessel card: name, flag, IMO, MMSI, type, DWT
- Sanctions status banner (red if sanctioned)
- Route history table (lat/lon/speed/timestamp for last 10 points)
- `EntityGraphSection` with graph data
- Note: do NOT add Leaflet.js — keep it simple for now, a table of route points is sufficient

All new components follow the existing CSS class names (`info-card`, `status-badge`, etc.) defined in `App.css`.

**Dark theme reference** (match existing styles from `App.css`):
- Background: `#0a0e17`, card background: `#161b22`, border: `#30363d`
- Text: `#e6edf3` primary, `#8b949e` secondary
- Green accent: `#3fb950`, red accent: `#f85149`, blue accent: `#58a6ff`

---

### Step 5 — LLM narrative generation (~4h)
**File:** `src/api.py` — add a `_generate_narrative()` helper and call it from each endpoint

Add near the top of `api.py`:
```python
_anthropic_client = anthropic.AsyncAnthropic(api_key=config.anthropic_api_key)

async def _generate_narrative(prompt: str) -> str:
    """Generate a 3–5 sentence analyst narrative from structured data."""
    try:
        response = await asyncio.wait_for(
            _anthropic_client.messages.create(
                model=config.model, max_tokens=500,
                messages=[{"role": "user", "content": prompt}]
            ),
            timeout=15.0
        )
        return response.content[0].text.strip()
    except Exception:
        return ""
```

Add a `narrative` field to the JSON returned by each of the 4 endpoints. Call `_generate_narrative()` concurrently with the data fetches (not after) using `asyncio.gather()` so it doesn't add latency.

**Prompt design principles (apply to all 4):**
- State what sources were searched, not just what was found
- "No findings" is an analytical result: "No derogatory information found across [sources] as of [date]"
- Include a confidence qualifier tied to source coverage
- Keep to 3–5 sentences; no bullet lists; write for a decision-maker, not a data engineer

**Prompt for each type:**

*Company/sanctions:*
```
You are an economic warfare analyst. Given the following data about {company_name}, write a 3-5 sentence
risk narrative covering: (1) current sanctions status, (2) likely stock price trajectory based on the
comparable cases, (3) key supply chain or investor exposure that constitutes friendly fire risk.
Data: {json_summary}
Confidence qualifier: {comparable_count} comparable cases used, data as of {date}.
```

*Person risk:*
```
You are an economic warfare analyst writing a due diligence summary for {name}.
Sources searched: OpenSanctions, OFAC SDN, OpenCorporates (corporate affiliations),
ICIJ Offshore Leaks, GDELT (recent news). Data as of {date}.
Findings: {json_summary}
Write 3-5 sentences characterizing this individual's risk profile. If no derogatory findings
were found, state that clearly and note what the clean profile means analytically.
```

*Sector risk:*
```
You are an economic warfare analyst. Given the following data on the {sector} sector,
write 3-5 sentences identifying the most significant risk vectors: entity sanctions exposure,
supply chain concentration, geopolitical exposure, and regulatory trajectory.
Data: {json_summary}
```

*Vessel risk:*
```
You are a maritime intelligence analyst. Given the following vessel intelligence for {vessel_name},
write 3-5 sentences characterizing the risk profile: sanctions status, flag-of-convenience
indicators, ownership opacity, and any dark shipping patterns evident from route history.
Data: {json_summary}
```

---

### Step 6 — Validate full orchestrator (~3h)
**File:** `src/api.py` + `src/orchestrator/main.py`

The `/api/analyze` endpoint already exists and calls `Orchestrator.analyze()`. Confirm it runs end-to-end:

```bash
conda activate econ312
curl -s -X POST http://localhost:8000/api/analyze/sync \
  -H "Content-Type: application/json" \
  -d '{"query":"What is the relationship between COSCO and Chinese military port access?"}' \
  | python -m json.tool
```

If it fails, check:
1. `config.validate()` passes (ANTHROPIC_API_KEY present)
2. `ToolRegistry._ensure_loaded()` imports all tool modules without errors
3. The decomposition prompt returns valid JSON (check `_extract_json()` in `orchestrator/main.py`)

If the orchestrator works, add a **progress panel** to the frontend for `/api/analyze`:
- User submits a complex query → `POST /api/analyze` → get `analysis_id`
- Poll `GET /api/analyze/{analysis_id}` every 2 seconds
- Show `progress[]` array as a log stream
- When `status == "completed"`, render the `ImpactAssessment` using the same 4-view component set

---

## File Map — Where Everything Lives

```
src/
  api.py                          ← ALL 4 backend endpoints + inline HTML fallback
  sanctions_impact.py             ← comparables dataset + projection logic
  orchestrator/
    main.py                       ← Orchestrator class (decompose/execute/synthesize)
    prompts.py                    ← SYSTEM_PROMPT, DECOMPOSITION_PROMPT, SYNTHESIS_PROMPT
    tool_registry.py              ← maps tool names → async functions
    entity_resolver.py            ← Claude classifier: company/person/sector/vessel
  common/
    types.py                      ← ALL shared Pydantic models (source of truth)
    config.py                     ← .env config loader
    cache.py                      ← get_cached() / set_cached() (diskcache)
    http_client.py                ← fetch_json() / post_json() with retry
  tools/
    sanctions/server.py           ← search_sanctions, check_sanctions_status, get_sanctions_proximity, get_recent_designations
    corporate/server.py           ← search_entity, get_corporate_tree, get_beneficial_owners, get_offshore_connections, resolve_entity
    market/server.py              ← get_stock_profile, get_price_history, get_institutional_holders, get_market_exposure, get_macro_indicator, search_market_entity
    trade/server.py               ← get_bilateral_trade, get_commodity_trade, get_supply_chain_exposure, get_trade_partners, get_shipping_connectivity
    geopolitical/server.py        ← search_events, get_conflict_data, get_risk_profile, get_bilateral_tensions, get_event_timeline
    economic/server.py            ← get_country_profile, get_gdp_exposure, get_commodity_prices, get_macro_series, estimate_sanction_impact
    vessels/client.py             ← vessel_find, vessel_by_mmsi, vessel_by_imo, vessel_history (NO server.py — called directly from api.py)
    screening/client.py           ← search_csl (called from sanctions_impact.py only)
  fusion/
    graph_builder.py              ← EntityGraph extraction from tool results
    renderer.py                   ← markdown / JSON / vis.js rendering
data/
  cache/                          ← diskcache files (auto-managed, safe to delete to reset)
  fixtures/                       ← (to be created) vessels.json fixture set

frontend/                         ← git submodule — React 18 + TypeScript + Vite
  package.json                    ← deps: react, chart.js, vis-network, typescript, vite
  vite.config.ts                  ← proxies /api/* → http://localhost:8000
  src/
    App.tsx                       ← top-level component — orchestrates all views
    api.ts                        ← fetch wrappers for backend endpoints
    types.ts                      ← TypeScript interfaces for all API responses
    components/
      QueryBox.tsx                ← search bar + example chips (has KNOWN_MAP ticker extractor)
      ImpactChart.tsx             ← Chart.js projection chart (company view)
      ComparablesTable.tsx        ← comparable cases table (company view)
      ImpactInfoCards.tsx         ← target info cards (company view)
      ProjectionSummary.tsx       ← 30/60/90d summary cards (company view)
      EntityGraphSection.tsx      ← vis.js graph wrapper — COMMENTED OUT, needs re-enabling
      GraphViewer.tsx             ← vis.js Network component
      ProgressPanel.tsx           ← step-by-step progress log
      Header.tsx                  ← page header
```

---

## Known Gotchas

1. **ACLED token** — `src/api.py` calls `refresh_acled_token()` on startup. The refresh token in `.env` may have expired (git log shows "refresh token revoked" fixes in history). If geopolitical tool calls return empty, this is why. The `ACLED_PASSWORD` in `.env` can be used to re-authenticate.

2. **Two frontends — don't confuse them.** The React app in `frontend/` is the development target. The inline HTML string in `_read_index_html()` in `src/api.py` is a legacy fallback served at `GET /`. They currently have the same features. New frontend work goes in the React app. The inline HTML does not need to be kept in sync — it's a demo safety net.

3. **`/api/analyze` is not commented out** — despite the comment at line 788 saying "commented out for demo", the actual `_run_analysis()` function and `start_analysis()` endpoint are present and active in the code. The comment is misleading. Smoke test it first.

4. **Sector fallback is silent** — if a sector isn't in `_SECTOR_COMPANIES`, `_match_sector()` returns semiconductor silently. A user asking about "Aircraft MRO" gets semiconductor data with no error. Fix this as part of Step 2.

5. **`build/lib/`** — there is a `build/lib/` directory with copies of source files. These are stale build artifacts. Do not edit them. The live code is in `src/`.

6. **Two branches** — `demo_stock_price_4` is the active branch. `master` has an earlier state. Stay on `demo_stock_price_4`.

7. **The `frontend/` submodule is the dev target, not the inline HTML.** The React app (`frontend/src/`) is where new frontend work belongs. Run it with `npm run dev` in the `frontend/` directory — it proxies `/api/*` to the FastAPI backend. The inline HTML in `api.py` is a fallback for single-server deployment; it does not need to be kept in sync with the React app.

8. **`EntityGraphSection` is already built but commented out** in `App.tsx` (lines 10, 150) and `App.tsx` imports it with a comment. `GraphViewer.tsx` is also complete. Uncomment and wire up rather than rewriting from scratch.

9. **`QueryBox.extractTicker()`** contains a hardcoded `KNOWN_MAP` that converts company names to tickers. This only works for company queries. For person/sector/vessel queries, the raw input should be passed through unchanged — update this function as part of the multi-mode work.

10. **Default model is `claude-sonnet-4-20250514` (Sonnet 4.5), not 4.6.** `src/common/config.py` hardcodes this as the default. Set `CLAUDE_MODEL=claude-sonnet-4-6` in `.env` to use the latest model. All LLM calls (orchestrator, entity resolver, narrative generation) respect this setting.

11. **`theme` entity type mismatch between Python and TypeScript.** `src/fusion/renderer.py` uses a `"theme"` entity type (orange, `#F0883E`) for ICIJ Offshore Leaks nodes. `frontend/src/components/EntityGraphSection.tsx` only has 6 legend entries and is missing `"theme"`. ICIJ nodes will render orange but have no legend label. Fix by adding `{ label: 'Theme/Offshore', color: '#F0883E' }` to the `LEGEND` array.

12. **`/api/resolve-entity` adds ~1–2s latency.** Do not call it unconditionally on every query submission. Use client-side classification first: 9-digit numbers → vessel (MMSI), 7-digit numbers / "IMO" prefix → vessel, known sector keywords → sector. Only call the API for genuinely ambiguous natural-language queries.

---

## Reference Documents

- `DEMO_PLAN.md` — full implementation plan with design rationale
- `ARCHITECTURE.md` — technical deep dive with code pointers (file:line references)
- `TOOL_REFERENCE.md` — all 26 tool function signatures, parameters, return types
- `CLAUDE.md` — quick reference: commands, conventions, project structure
- `plan.md` — original design document (historical context, data source survey)
