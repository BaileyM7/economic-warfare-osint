# Tool Reference — Economic Warfare OSINT

All 30 MCP tool functions registered in `src/orchestrator/tool_registry.py` (4 sanctions + 5 corporate + 6 market + 5 trade + 5 geopolitical + 5 economic). Each returns a `ToolResponse` envelope with `data`, `confidence` (HIGH/MEDIUM/LOW), `sources`, and `errors`.

---

## Sanctions & Watchlist (`src/tools/sanctions/`)

**Sources:** OpenSanctions API · OFAC SDN CSV (downloaded + parsed)

### `search_sanctions(query, entity_type="any")`
Search across OpenSanctions + OFAC for sanctioned entities.
- `query`: name or identifier (company, person, vessel, aircraft)
- `entity_type`: `"person"` | `"company"` | `"vessel"` | `"aircraft"` | `"any"`
- Returns: `{query, matches: [SanctionEntry], total_matches}`
- Confidence: driven by match score (≥0.9 → HIGH, ≥0.6 → MEDIUM, else LOW)

### `check_sanctions_status(entity_name)`
Determine if an entity is currently sanctioned and on which lists.
- Returns: `{entity_name, is_sanctioned, lists_found, programs, entries}`
- Confidence: HIGH if sanctioned and ≥2 entries; MEDIUM otherwise

### `get_sanctions_proximity(entity_name, max_hops=3)`
Degrees of separation from the entity to nearest sanctioned entities via OpenSanctions relationship graph.
- `max_hops`: 1–5 (default 3; higher = slower)
- Returns: `{query_entity, nodes, edges, nearest_sanctioned_hop, sanctioned_neighbors}`
- Confidence: HIGH if hop=0 (direct match); MEDIUM if >3 nodes in graph

### `get_recent_designations(days=30)`
OFAC SDN entries with designation dates within the lookback window.
- `days`: 1–365 (default 30)
- Returns: `{days, designations: [RecentDesignation], count}`
- Confidence: MEDIUM (OFAC date extraction from remarks is best-effort)

---

## Corporate Graph (`src/tools/corporate/`)

**Sources:** OpenCorporates · GLEIF (LEI registry) · ICIJ Offshore Leaks

### `search_entity(query)`
Find a company across OpenCorporates, GLEIF, and ICIJ.
- Returns: `{query, companies: [CompanyRecord], lei_records: [LEIRecord], icij_results}`

### `get_corporate_tree(entity_name)`
Ownership chain — parent companies and subsidiaries.
- Returns: `CorporateTree` with parent chain and subsidiary list, sourced from GLEIF hierarchy + OpenCorporates

### `get_beneficial_owners(entity_name)`
Officers and ultimate beneficial owners (UBO).
- Returns: `BeneficialOwnerResult` with officers list and UBO chain

### `get_offshore_connections(entity_name)`
Search ICIJ Offshore Leaks for Panama Papers / Pandora Papers connections.
- Returns: ICIJ entity matches with offshore jurisdiction flags

### `resolve_entity(name, jurisdiction=None)`
Cross-source entity resolution — finds canonical LEI, OpenCorporates ID, and registry number.
- Returns: unified `EntityResolution` record with all matched identifiers

---

## Market Data (`src/tools/market/`)

**Sources:** yfinance · SEC EDGAR 13F filings · FRED (Federal Reserve Economic Data)

### `get_stock_profile(ticker)`
Company overview + current price from Yahoo Finance.
- Returns: `StockProfile` (name, sector, market cap, current price, description)

### `get_price_history(ticker, period="1y")`
Historical OHLCV price data.
- `period`: yfinance period string (`"1mo"`, `"3mo"`, `"1y"`, `"5y"`, etc.)
- Returns: `[PriceData]` with date, open, high, low, close, volume

### `get_institutional_holders(ticker)`
13F-reported institutional holders (pension funds, asset managers, etc.).
- Returns: `[InstitutionalHolder]` with holder name, shares, value, date

### `get_market_exposure(entity_name)`
Full US/allied institutional exposure analysis — the primary "friendly fire" market check.
- Finds ticker, fetches holders, estimates total exposed capital
- Returns: `ExposureReport` with per-institution breakdown and total exposure estimate

### `get_macro_indicator(series_id, period="5y")`
FRED time series data.
- `series_id`: FRED series ID (e.g., `"DCOILWTICO"` for WTI crude, `"DGS10"` for 10yr Treasury)
- Returns: time series with dates and values

### `search_market_entity(query)`
Find ticker symbol and SEC CIK for a company name.
- Returns: `{ticker, cik, company_name, exchange}` — entry point before other market tools

---

## Trade Flows (`src/tools/trade/`)

**Sources:** UN Comtrade API (500 calls/day free tier) · UNCTADstat

### `get_bilateral_trade(reporter, partner, year=2023)`
Annual merchandise trade between two countries.
- `reporter`, `partner`: ISO-3 country codes (e.g., `"CHN"`, `"USA"`)
- Returns: `[TradeFlow]` with commodity codes, values, quantities, flow direction

### `get_commodity_trade(commodity_code, reporter="", year=2023)`
Who trades a specific commodity with a country.
- `commodity_code`: HS code (e.g., `"8542"` for semiconductors)
- Returns: `[TradeFlow]` by partner country

### `get_supply_chain_exposure(country, commodity_code)`
Import dependency analysis — what fraction of a commodity does a country import and from where.
- Returns: `CommodityDependency` with concentration index and top suppliers

### `get_trade_partners(country, flow="import", year=2023)`
Top trade partners ranked by value.
- `flow`: `"import"` | `"export"` (singular — not "imports"/"exports")
- Returns: `[TradePartnerSummary]` with partner, value, share of total

### `get_shipping_connectivity(country)`
Maritime Liner Shipping Connectivity Index (LSCI) from UNCTADstat.
- Returns: `ShippingConnectivity` with LSCI score, trend, and bilateral connectivity scores

---

## Geopolitical Context (`src/tools/geopolitical/`)

**Sources:** GDELT 2.0 (near-real-time, every 15 min) · ACLED (registration required)

### `search_events(query, days=30)`
Recent GDELT news events matching a query.
- Returns: `[GdeltEvent]` with article URLs, tones, themes, locations, actors

### `get_conflict_data(country, days=90)`
ACLED conflict events for a country (political violence, protests, battles).
- Returns: `ConflictSummary` with event counts by type, fatalities, actor list

### `get_risk_profile(country)`
Combined geopolitical risk assessment drawing on GDELT tone + ACLED event counts.
- Returns: `GeopoliticalRiskProfile` with overall risk score (LOW/MEDIUM/HIGH/CRITICAL), sub-scores by category, recent drivers

### `get_bilateral_tensions(country1, country2, days=90)`
Events and tone between two countries from GDELT.
- Returns: `BilateralTensionReport` with event timeline, tone trend, tension level

### `get_event_timeline(query, days=90)`
Event intensity over time for a query — useful for spotting escalation patterns.
- Returns: `[{date, event_count, avg_tone}]` time series

---

## Economic Modeling (`src/tools/economic/`)

**Sources:** FRED · IMF Data Portal · World Bank Open Data

### `get_country_profile(country)`
Comprehensive economic snapshot for a country.
- Returns: `CountryEconomicProfile` — GDP, GDP per capita, inflation, unemployment, trade openness, FX reserves, credit rating

### `get_gdp_exposure(country, sector=None)`
GDP size and (optionally) sector composition.
- Returns: `EconomicIndicator` time series for GDP; if `sector` provided, adds sector share

### `get_commodity_prices(commodity, period="5y")`
Historical commodity price data from FRED or World Bank Pink Sheet.
- `commodity`: `"oil"` | `"gold"` | `"copper"` | `"wheat"` | `"natural_gas"` | etc.
- Returns: `[CommodityPrice]` with date, price, currency, unit

### `get_macro_series(indicator, country, years=5)`
Macro time series from IMF or World Bank.
- `indicator`: `"gdp"` | `"gdp_growth"` | `"inflation"` | `"unemployment"` | `"fdi"` | `"reserves"` | `"debt_to_gdp"` | `"exports"` | `"imports"` | `"current_account"` | `"trade_pct_gdp"`
- Returns: `[EconomicIndicator]` annual series

### `estimate_sanction_impact(target_country, sanction_type)`
Heuristic GDP/trade impact estimate. **Not a rigorous econometric model** — order-of-magnitude screening only. Uses hardcoded country profiles for RUS, CHN, IRN, PRK, VEN, SYR, CUB, MMR, TUR; falls back to generic estimates for others.
- `sanction_type`: `"comprehensive"` | `"sectoral"` | `"financial"` | `"energy"` | `"technology"` | `"individual"` | `"trade"` | `"arms"`
- Formula: `gdp_impact_pct = trade_share × multiplier × (1 − openness) × 100`; energy multiplier amplified by `energy_dependence` factor
- Returns: `SanctionImpactEstimate` with gdp_impact_pct, trade_impact_usd, sectors_affected, model_inputs

---

## Tool Call Format for Research Plans

When the orchestrator's DECOMPOSITION_PROMPT generates a research plan, tools are specified as:

```json
[
  {
    "step": 1,
    "description": "Identify entity and check sanctions status",
    "tools": [
      {"tool": "search_sanctions", "params": {"query": "Huawei", "entity_type": "company"}},
      {"tool": "search_entity", "params": {"query": "Huawei"}}
    ],
    "depends_on": []
  },
  {
    "step": 2,
    "description": "Map corporate structure and market exposure",
    "tools": [
      {"tool": "get_corporate_tree", "params": {"entity_name": "Huawei"}},
      {"tool": "get_market_exposure", "params": {"entity_name": "Huawei"}}
    ],
    "depends_on": [1]
  }
]
```

Steps with empty `depends_on` run in parallel. Steps with dependencies wait for their prerequisites to complete.

---

## Tool Response Envelope

Every tool returns this exact shape (serialized from `ToolResponse`):

```json
{
  "data": { ... },
  "confidence": "HIGH" | "MEDIUM" | "LOW",
  "sources": [
    {
      "name": "OpenSanctions",
      "url": "https://api.opensanctions.org/",
      "accessed_at": "2026-03-28T12:00:00",
      "dataset_version": null
    }
  ],
  "timestamp": "2026-03-28T12:00:00",
  "errors": []
}
```

Tools never raise exceptions to the orchestrator — failures go into `errors[]` with `confidence: LOW`.

---

## Additional Clients (Not in ToolRegistry)

These are available directly in `src/api.py` and called from specific endpoints:

**`src/tools/vessels/client.py`**
- `vessel_find(name)` — search vessel by name (Datalastic)
- `vessel_by_mmsi(mmsi)` — lookup by MMSI
- `vessel_by_imo(imo)` — lookup by IMO number
- `vessel_history(mmsi, days)` — AIS position history

**`src/tools/screening/client.py`**
- `search_csl(query)` — Consolidated Sanctions List search (combines OFAC + BIS + DDTC)

**`src/tools/corporate/client.py`** (direct imports in api.py)
- `gleif_search_lei(query)` — search GLEIF by company name
- `gleif_get_direct_parent(lei)` — direct parent in GLEIF hierarchy
- `gleif_get_ultimate_parent(lei)` — ultimate parent in GLEIF hierarchy
- `oc_search_officers(name)` — search OpenCorporates for officer/director
- `icij_search(query)` — ICIJ Offshore Leaks search
