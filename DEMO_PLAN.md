# Demo Implementation Plan

## Framing

The four example queries — SMCI sanction impact, a CEO risk profile, the Aircraft MRO sector, and a vessel search — are **representative of query classes**, not the specific targets we are optimizing for. The goal is a system that handles any well-formed query of each type with consistent quality. SMCI, Guy de Carufel, Aircraft MRO, and an unnamed vessel are just concrete tests to validate that each mode works on a realistic input.

The four query classes map directly to four existing API endpoints:

| Query class | Endpoint | Accepts |
|-------------|----------|---------|
| "What if we sanction `<company>`?" | `POST /api/sanctions-impact` | Any ticker or company name |
| "Risk factors for `<person>`" | `POST /api/person-profile` | Any named individual |
| "Risk factors in the `<sector>` sector" | `POST /api/sector-analysis` | Any industry / sector |
| "`<vessel name / IMO / MMSI>`" | `POST /api/vessel-track` | Name, 7-digit IMO, 9-digit MMSI |

All 4 endpoints already exist with real data behind them. The gaps are **presentation**, **domain coverage**, and **LLM synthesis** — not infrastructure.

---

## Current State — What Works vs. What Doesn't

### What works today
- All 6 tool domains make real API calls (sanctions, corporate, market, trade, geopolitical, economic)
- `/api/sanctions-impact` runs end-to-end for any ticker — chart + comparables + OFAC check
- `/api/person-profile` fetches real data from 5 sources for any name
- `/api/sector-analysis` runs OFAC checks for key players in 5 pre-defined sectors
- `/api/vessel-track` runs OFAC check on vessel name; AIS lookup fires but falls back to mock
- Entity graph endpoint builds real GLEIF + OFAC + sector peer graphs for any company
- Disk caching respects free-tier rate limits across all tools

### What doesn't work
- **Frontend only shows the sanctions impact chart** — person, sector, and vessel results are returned but never displayed
- **Sector coverage is narrow** — only semiconductor, energy, shipping, rare earth, telecom. Any other sector returns a semiconductor fallback
- **Comparables dataset is narrow** — all 12 cases are Chinese-listed tech ADRs being sanctioned by the US; wrong profile for US companies facing export controls or for non-tech sectors
- **Vessel AIS is mock** — `DATALASTIC_API_KEY` is empty; client returns fake coordinates with a "demo" watermark
- **No LLM narratives** — all 4 endpoints return structured JSON but no natural-language analysis; a clean profile looks like an empty result without synthesis
- **`/api/analyze` (full orchestrator)** — the code exists and is wired, but hasn't been validated end-to-end

---

## Gap Analysis by Query Class

### Company / Sanctions Impact
The chart and comparable curves work. The quality problem is the **comparables dataset** — 11 entries, all Chinese-listed or US-listed Chinese ADRs affected by US/Chinese regulatory actions (ZTE, Alibaba, Xiaomi, Full Truck Alliance, Tencent Music, Bilibili, NIO, PDD, Baidu, Micron, KWEB). This is the wrong profile for US companies facing export controls, financial institutions, energy companies, etc.

Real queries will span archetypes with distinct price impact signatures:
- US company under export control action or Entity List designation
- European company caught in secondary sanctions
- Financial institution cut off from SWIFT
- State-owned energy company under sectoral sanctions
- Defense contractor designated under CAATSA

**Fix:** Add a `sanction_type` field to each existing comparable dict and add new entries covering other archetypes. The existing `SECTOR_GROUPS` dict in `sanctions_impact.py` already handles sector-based filtering — build on that pattern rather than restructuring the data shape. At query time, filter by both `sector` and `sanction_type` when both can be inferred.

### Person Risk Profile
The endpoint is fully implemented and works for any name. Quality varies by how public the individual is:
- **Public figures with derogatory records** (oligarchs, designated officials): high-confidence results from OpenSanctions + OFAC + ICIJ
- **Semi-public executives** (e.g., defense sector CEOs, dual-use tech founders): real corporate affiliations from OpenCorporates, sparse OSINT otherwise
- **Truly private individuals**: mostly clean results — the value is the LLM narrative explaining what was searched and what absence of findings means

The pattern "no red flags found" is itself analytically meaningful and must be rendered clearly rather than appearing as a blank result.

**Fix:** LLM synthesis is mandatory here, not optional. Also need to enrich the person endpoint with company-level risk context — if someone is affiliated with a high-risk company or sector, that should surface even if the individual themselves is clean.

### Sector Analysis
The current implementation hardcodes 5 sectors and returns a fallback to semiconductor for anything else. This breaks immediately on any real query outside that list.

The fix is twofold:
1. **Expand the sector registry** with enough coverage to handle common defense/security-relevant sectors
2. **Make unfamiliar sectors work gracefully** — if a sector isn't in the registry, use the LLM to identify relevant companies + HS codes rather than returning a fallback

The sectors most likely to come up in real queries:
- Defense / aerospace primes
- Aircraft MRO
- Critical minerals / rare earth processing
- Port and logistics infrastructure
- Financial services / correspondent banking
- Pharmaceutical / precursor chemicals
- Dual-use software / surveillance tech
- Commercial satellite / space

**Fix:** Expand registry to ~15 sectors; add LLM-assisted sector resolution for unknowns; wire trade + geopolitical tools into the sector endpoint.

### Vessel Tracking
The OFAC check is real and catches any vessel on the SDN list. The AIS data is the problem — without a Datalastic key, position and route history are always fake.

For demo purposes, the most analytically interesting vessel queries are **sanctioned vessels** (dark shipping, Iranian tankers, oligarch yachts, DPRK freighters) — and for those, OpenSanctions vessel schema provides ownership, flag, IMO, and associated entities for free. The gap is live AIS.

For live AIS, three options:
1. **MyShipTracking** free API — limited calls/month but works for demos
2. **VesselFinder** public data — scrape-able for specific IMOs
3. **Curate a small fixture set** of 5–6 vessels with realistic (real but slightly stale) AIS data stored as JSON — enough to demonstrate the UI without needing live calls

**Fix:** Add OpenSanctions vessel-schema search as primary source; add one free AIS tier as secondary; add a fixture set for known high-value demo vessels.

---

## Implementation Phases

### Phase 0 — Smoke Test & Wire-Up
> Validate what actually runs before building anything new.

1. Call each of the 4 endpoints with a representative input per query class; record actual responses
2. Validate `/api/analyze` (full orchestrator) end-to-end — the code is present but annotated "commented out for demo"
3. Log any broken imports, missing dependencies, or silent failures
4. Confirm all 6 tool domains return non-empty results against live APIs

**Deliverable:** `SMOKE_TEST.md` documenting actual response shapes, latency, and failure modes per endpoint.

---

### Phase 1 — Domain Coverage

#### 1A. Comparables — add `sanction_type` field and new entries
The existing `SANCTIONS_COMPARABLES` list has 11 entries and a `SECTOR_GROUPS` dict for sector filtering. Do not restructure the data shape — add a `sanction_type` string field to each existing entry, then append new entries for missing archetypes.

Existing entries to tag:
- ZTE, Alibaba, Xiaomi, Full Truck Alliance, Tencent Music, Bilibili, NIO, PDD, Baidu, KWEB → `"ofac_ccmc"` (OFAC/CCMC designation or regulatory crackdown)
- Micron → `"retaliation"` (foreign government retaliatory ban)

New entries to add by archetype:

| Name | Ticker | Date | `sanction_type` | Why |
|------|--------|------|-----------------|-----|
| Nvidia | NVDA | 2022-10-07 | `"us_export_control"` | BIS advanced chip export restriction |
| Applied Materials | AMAT | 2022-10-07 | `"us_export_control"` | Same BIS rule, fab equipment |
| ASML | ASML | 2023-01-28 | `"us_export_control"` | Dutch EUV export license revoked |
| Qualcomm | QCOM | 2019-05-15 | `"us_export_control"` | Huawei supply ban |
| Seagate | STX | 2023-04-19 | `"bis_penalty"` | BIS fine for Huawei sales |
| Gazprom | OGZPY | 2022-02-24 | `"sectoral"` | EU/US energy sanctions |
| Sberbank ADR | SBRCY | 2022-02-24 | `"swift_cutoff"` | SWIFT exclusion |

Update `get_comparable_curves()` to also filter by `sanction_type` when provided, using the same ≥3-match fallback logic that already exists for sector filtering.

#### 1B. Sector registry expansion
Expand `_SECTOR_COMPANIES` in `api.py` to cover the sectors most likely to appear in real queries. Priority additions:

| Sector key | Aliases to match | Example companies |
|------------|-----------------|-------------------|
| `aircraft_mro` | mro, aviation maintenance, aircraft repair | AAR Corp, Heico, TransDigm, Lufthansa Technik, ST Engineering, HAECO |
| `defense_aerospace` | defense, defense primes, aerospace | Lockheed Martin, RTX, Northrop, BAE Systems, L3Harris, Leonardo, Thales |
| `critical_minerals` | rare earth processing, lithium, cobalt | MP Materials, Lynas, Albemarle, Ganfeng, CNGR, Pilbara |
| `port_logistics` | port, logistics infrastructure, shipping infrastructure | Hutchison Ports, DP World, COSCO Ports, PSA International |
| `financial` | banking, correspondent banking, finance | Sberbank, VTB, Bank of China, HSBC, Standard Chartered |
| `pharma_precursor` | pharmaceutical, precursor chemicals, API | Generic API suppliers, listed precursor chemical companies |
| `dual_use_tech` | surveillance tech, AI weapons, dual-use software | DJI, Hikvision, Dahua, Megvii, SenseTime |
| `space_satellite` | space, satellite, commercial space | SpaceX (private), Planet Labs, Maxar, CASC, Iridium |

Also replace the hardcoded fuzzy-match function with an LLM-assisted classifier that can handle any sector string, falling back to the registry only when confident.

#### 1C. Vessel data — real sources
Add in priority order to `src/tools/vessels/client.py`:

1. **OpenSanctions vessel schema** (`schema=Vessel`) — free, covers ~8,000 designated vessels across OFAC/EU/UN/OFSI lists; returns IMO, flag, owner entities, and sanction programs. This is the primary source for the analytically interesting case (sanctioned vessels).

2. **MyShipTracking free API** — basic vessel lookup by IMO/MMSI; provides last-known position and current voyage. Covers the non-sanctioned vessel lookup case.

3. **Fixture set** — 6–8 vessels curated for demo (e.g., "Lana" Iranian tanker, "STS Yenisei" Russian LNG transshipment, a DPRK-flagged freighter, a flagged oligarch yacht) with realistic position data stored in `data/fixtures/vessels.json`. Used when both live sources fail.

Remove the existing mock-with-demo-note pattern entirely.

---

### Phase 2 — Frontend: Multi-Mode Interface
The SPA currently only renders the sanctions impact chart. Restructure it as a single search bar with **mode detection** — the user types a query, the system classifies it, and renders the appropriate view.

**Mode detection flow (two-tier to minimize latency):**

```
User types query
  → Tier 1: client-side classification (instant, no API call)
      - 9-digit number → vessel (MMSI)
      - 7-digit number or starts with "IMO" → vessel
      - known sector keywords → sector
      - otherwise → ambiguous
  → Tier 2: POST /api/resolve-entity (only for ambiguous queries)
      - Claude classifies: "company" | "person" | "sector" | "vessel"
  → render matching view
```

Calling `/api/resolve-entity` unconditionally adds ~1–2 seconds of Claude API latency before any data loads. The client-side tier catches unambiguous cases (IMO numbers, MMSI, known sector terms) without a round-trip. Only genuinely ambiguous natural-language queries need the Claude classifier.

The `/api/resolve-entity` endpoint already exists and works. Add client-side pre-classification in `QueryBox.tsx` before calling it.

**Four views to build:**

**Company view** (sanctions impact):
- Price projection chart (existing) + comparables table (existing)
- Entity graph sidebar (GLEIF + OFAC network)
- Supply chain summary panel (trade tool output)
- LLM narrative paragraph below the chart
- Loads example: any ticker or company name

**Person view** (risk profile):
- Profile header: name, sanctions status badge, nationality, DOB if available
- Affiliations table: company, role, jurisdiction, active/inactive
- Offshore connections panel (ICIJ — shown only if non-empty, not as an empty section)
- Recent events panel (GDELT — title, date, tone badge; "No recent coverage" if empty)
- vis.js relationship graph
- LLM narrative at top explaining what was searched and what was found (critical for clean profiles)
- Loads example: any person name

**Sector view** (risk analysis):
- Risk headline card: sector name, sanctioned player count, key supply dependencies
- Company table: name, country flag, ticker, sanctions status badge
- Trade dependency panel: top 2–3 commodity/supply chain exposures
- Geopolitical context: bilateral tensions relevant to sector
- vis.js sector graph
- LLM narrative: top 3–5 risk factors specific to this sector
- Loads example: any sector string

**Vessel view** (vessel intelligence):
- Vessel identity card: name, IMO, MMSI, flag, vessel type, DWT
- Sanctions status banner (prominent, red if hit)
- Last-known position / route map (Leaflet.js)
- Ownership chain vis.js graph
- LLM narrative: risk characterization including flag-of-convenience risk, owner sanctions exposure
- Loads example: vessel name, IMO, or MMSI

---

### Phase 3 — LLM Synthesis Narratives
Each endpoint returns structured data but no natural-language analysis. Add a `narrative` field to every response.

**Implementation pattern** (shared across all 4 endpoints):
```python
async def _generate_narrative(data: dict, scenario_type: str) -> str:
    prompt = NARRATIVE_PROMPTS[scenario_type].format_map(data)
    response = await anthropic_client.messages.create(
        model=config.model, max_tokens=500,
        messages=[{"role": "user", "content": prompt}]
    )
    return response.content[0].text
```

**Prompts to write — design principles:**
- Always state what sources were searched, not just what was found
- Absence of findings is meaningful: "No derogatory information found across X sources as of [date]" is an analytical conclusion
- Always include a confidence qualifier tied to data freshness and source coverage
- For persons: note whether the individual's public footprint is thin (which itself warrants attention in some contexts)
- For sectors: distinguish between entity-level sanctions risk and structural supply-chain risk — they require different responses

**Narrative types:**
- `company_sanctions`: explain the sanction mechanism, likely price trajectory based on comps, key supply chain and investor exposure (friendly fire)
- `person_risk`: characterize the individual's risk profile across financial crime, sanctions, corporate, and reputational dimensions; explain what clean results mean
- `sector_risk`: identify the 3–5 most significant risk vectors (entity sanctions, supply chain concentration, geopolitical exposure, regulatory trajectory)
- `vessel_risk`: characterize ownership risk, flag-of-convenience indicators, dark shipping patterns if route history shows gaps, sanctions exposure

---

### Phase 4 — Full Orchestrator for Open-Ended Queries
The four structured endpoints handle well-formed queries of known types. The `/api/analyze` endpoint handles everything else — compound questions, cross-entity analysis, scenario modeling, and queries that span multiple query classes.

Examples that need the full orchestrator:
- "What is the relationship between Cognitive Space and the Canadian government's satellite programs?"
- "Which US pension funds have exposure to Supermicro's supply chain?"
- "Map the ownership chain from a specific vessel back to its beneficial owner"
- "What happens to Taiwan's semiconductor capacity if we escalate sanctions on SMIC?"

**Steps:**
1. Fix the background runner in `api.py` (present but annotated "commented out for demo")
2. Add a live progress panel to the frontend that streams the `progress[]` array via polling or WebSocket
3. The orchestrator result (`ImpactAssessment`) renders using the same component set as the 4 structured views — findings map to the narrative panel, entity_graph to the vis.js graph, friendly_fire to a dedicated alert panel
4. Route the simple demo queries through the structured endpoints; use `/api/analyze` for anything that doesn't match a known query class

---

## Priority Order

| Priority | Item | Effort | What it enables |
|----------|------|--------|----------------|
| 1 | Smoke test all 5 endpoints with real inputs | 2h | Know the baseline |
| 2 | Refactor comparables by sanction archetype | 3h | Accurate projections for any company type |
| 3 | Expand sector registry to ~15 sectors + LLM fallback | 3h | Any sector query works |
| 4 | Add OpenSanctions vessel schema + fixture set | 3h | Vessel queries on sanctioned ships work |
| 5 | Mode-detection single search bar + 4 views in frontend | 10h | All query classes are displayable |
| 6 | LLM narrative generation (all 4 endpoints) | 4h | Clean profiles read as analysis, not empty results |
| 7 | Wire trade + geopolitical tools into sector endpoint | 3h | Sector risk goes beyond entity sanctions |
| 8 | Fix + validate full orchestrator pipeline | 5h | Open-ended compound queries work |
| 9 | Progress streaming panel for orchestrator queries | 3h | Transparent reasoning for complex queries |

**Minimum viable demo (all query classes work):** items 1–6 (~25h).
**Full production quality:** all items (~36h).

---

## Key Design Decisions

### Mode detection vs. explicit tabs
Using entity type classification (via existing `/api/resolve-entity`) instead of explicit tabs means the user can type anything — "SMCI", "Guy de Carufel", "Aircraft MRO", "IMO 9780888" — and get the right view without needing to know which mode to select. This matches how a real analyst would use the tool.

### Comparables as a structured dataset, not a list
Restructuring comparables by `sanction_type` means the projection model will give defensible outputs for US companies under export controls, European companies under secondary sanctions, financial institutions, and energy companies — not just Chinese tech ADRs. This is the difference between a demo that works on one example and one that works on any realistic query.

### Sector registry + LLM fallback
The hardcoded sector dict can never cover all sectors an analyst might ask about. The right pattern is: registry for well-known sectors (fast, consistent), LLM for unknowns (slower, but correct). The LLM can identify the top 8–10 publicly-traded companies in any sector and appropriate HS codes for trade queries, then the result gets cached so repeated queries are fast.

### Vessel strategy: sanctioned-first
The analytically interesting vessel queries in this domain are almost always about sanctioned or suspected vessels — dark shipping, flags of convenience, owner obfuscation. OpenSanctions vessel schema covers this case for free. Live AIS (position, route) is secondary — important for the full picture but not blocking for a compelling demo. Build sanctioned-vessel search first; add live AIS later.

### Narrative is not optional
Without LLM synthesis, a clean person profile looks like a system failure ("no results"). With synthesis, it reads as "no derogatory findings across OpenSanctions, OFAC, ICIJ Offshore Leaks, and OpenCorporates as of [date], with moderate confidence. Limited public footprint may warrant additional source coverage." That's a real analytical output. The narrative layer is what separates a data dashboard from an analyst tool.
