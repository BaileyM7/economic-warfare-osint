# OSINT Agentic Workflow: Economic Warfare Exercise Support

## Overview

A multi-agent system that takes natural language economic warfare questions, autonomously gathers and fuses commercially available financial, corporate, and geopolitical data, and produces structured impact assessments or analyst-ready reports — all on UNCLASS infrastructure.

-----

## The Core Idea

An analyst or planner asks a question in plain language:

*“What happens to downstream supply chains if we sanction Norinco’s subsidiary in Malaysia?”*

The system decomposes that question into a research plan, dispatches specialized tool agents to pull from commercial data sources, fuses the results into a unified entity/relationship graph, runs the scenario logic, and returns either a structured report or machine-readable output for further workflow consumption.

-----

## Architecture: Three-Layer Model

### Layer 1 — Orchestrator Agent (The “Quarterback”)

The top-level LLM agent that owns the conversation and the research plan.

**Responsibilities:**


Interprets the analyst’s question and classifies the scenario type (sanction impact, investment interception, facility denial, supply chain disruption, etc.)
Decomposes the question into a directed acyclic graph (DAG) of subtasks
Dispatches subtasks to Layer 2 tool agents via MCP
Synthesizes results into final output
Handles ambiguity by asking clarifying questions *only when the data can’t resolve it*


**Key design decision:** The orchestrator doesn’t touch raw data. It reasons over structured returns from the tool layer. This keeps the context window clean and the agent focused on synthesis, not parsing.

-----

### Layer 2 — Specialized MCP Tool Agents

Each tool agent wraps a commercial data source and exposes a clean, typed interface via MCP. The orchestrator calls these like functions.

|MCP Tool                 |Source                                                 |What It Returns                                                                                                                            |
|-------------------------|-------------------------------------------------------|-------------------------------------------------------------------------------------------------------------------------------------------|
|**Corporate Graph**      |Sayari (UBO, foreign filings, corporate registries)    |Entity resolution, ownership chains, subsidiary trees, beneficial ownership to natural persons, jurisdiction mapping, flagged relationships|
|**Sanctions & Watchlist**|Kharon, C4ADS, OFAC/SDN, EU/UK lists                   |Current sanctions status, historical designations, proximity-to-sanctioned score (degrees of separation), evasion pattern indicators       |
|**Market Data**          |Refinitiv / Bloomberg                                  |Stock price, market cap, sector exposure, trading volume, institutional holders, historical volatility, analyst consensus                  |
|**Trade & Shipping**     |Import/export records, AIS/maritime data (if available)|Trade flow volumes, port activity, vessel ownership chains, commodity dependencies                                                         |
|**Geopolitical Context** |GDELT, ACLED, or curated OSINT feeds                   |Recent events, political risk signals, bilateral relationship indicators, BRI/MSR project tracking                                         |
|**Economic Modeling**    |Internal or third-party (e.g., Oxford Economics API)   |GDP exposure, sector multipliers, employment impact estimates, FDI flow data                                                               |

**MCP design principle:** Each tool returns a **typed, structured JSON response** with a confidence score and source provenance. The orchestrator never gets raw HTML or unstructured text from these tools — that’s the tool agent’s job to normalize.

-----

### Layer 3 — Fusion & Output Engine

Takes the orchestrator’s synthesized findings and renders them into the requested format.

**Output modes:**


**Structured Report** — Markdown or PDF brief with executive summary, entity network visualization, impact assessment, confidence levels, and source citations
**Structured Data** — JSON/STIX-like output for downstream ingestion into other tools, dashboards, or wargaming platforms
**Graph Export** — Neo4j-compatible or vis.js-ready node/edge data for interactive network exploration


-----

## Scenario Workflow: “What if we sanction Company X?”

Here’s how the system handles the canonical sanction-impact question:

```
ANALYST INPUT
"What would happen if we sanction Fujian Jinhua and their stock price tanks?"
     │
     ▼
┌─────────────────────────────────────┐
│  ORCHESTRATOR: Decompose Question   │
│                                     │
│  1. Resolve entity → Fujian Jinhua  │
│  2. Map corporate structure & UBO   │
│  3. Check current sanctions status  │
│  4. Pull market data & exposure     │
│  5. Identify supply chain links     │
│  6. Model downstream impact         │
│  7. Identify allied/partner equities│
│     at risk (friendly fire check)   │
└─────────┬───────────────────────────┘
          │
          ▼
┌─────────────────────────────────────┐
│  PARALLEL MCP TOOL CALLS            │
│                                     │
│  Sayari ──→ ownership tree,         │
│             subsidiaries in 7       │
│             jurisdictions, UBO to   │
│             3 natural persons       │
│                                     │
│  Kharon ──→ already on Entity List, │
│             2 subsidiaries not yet  │
│             designated              │
│                                     │
│  Refinitiv → $2.1B market cap,      │
│              top institutional      │
│              holders include 3 US   │
│              pension funds          │
│                                     │
│  Trade ───→ primary DRAM supplier   │
│             to 4 PRC OEMs, 12%      │
│             of regional capacity    │
└─────────┬───────────────────────────┘
          │
          ▼
┌─────────────────────────────────────┐
│  ORCHESTRATOR: Synthesize & Assess  │
│                                     │
│  Finding: Sanctioning parent alone  │
│  leaves 2 subs operational. 3 US   │
│  pension funds hold $140M exposure. │
│  DRAM supply disruption cascades to │
│  Lenovo, Xiaomi, and potentially    │
│  Samsung (friendly fire). Regional  │
│  capacity gap fillable by SK Hynix  │
│  within 18mo.                       │
│                                     │
│  Confidence: HIGH (entity data)     │
│              MEDIUM (supply chain)  │
│              LOW (market reaction)  │
└─────────┬───────────────────────────┘
          │
          ▼
┌─────────────────────────────────────┐
│  OUTPUT: Structured Report or JSON  │
└─────────────────────────────────────┘
```

-----

## The “Friendly Fire” Pattern

One of the highest-value features: every sanction or disruption scenario should automatically run a **reverse exposure check** — who on *our* side gets hurt?


US/allied institutional investors holding equity
Allied companies in the supply chain
Dual-use technology dependencies (e.g., sanctioning a chip fab that also supplies a NATO partner)
Port/logistics disruptions that affect allied shipping lanes


This turns the tool from a “what can we break” system into a **decision-quality wargaming aid**.

-----

## The “Investment Interception” Pattern

For questions like *“China is investing $30M in this port — how do we intersect?”*

The workflow shifts:


**Identify the investment vehicle** — Sayari traces the entity, its state-owned parent, and the financing structure
**Map the strategic context** — What else is nearby? Other BRI projects? Military dual-use potential?
**Identify intervention levers** — Alternative financing (DFC, EXIM, JBIC), competing bids, regulatory friction points in-country, multilateral pressure
**Model the timeline** — When does money move? What are the decision points? Where’s the latest point of intervention?


-----

## Key Design Decisions

**Why MCP tools vs. direct API calls?**
MCP gives you a clean abstraction layer. You can swap Sayari for a different UBO provider without touching the orchestrator. You can version tool schemas independently. And critically, you can **audit every tool call** — which matters when this feeds exercises or informs real planning.

**Why a single orchestrator vs. multi-agent swarm?**
For this use case, a single orchestrator with typed tool calls is more reliable and auditable than a swarm. Planners need deterministic-feeling outputs with clear provenance. A swarm architecture introduces coordination overhead and makes it harder to explain *why* the system reached a conclusion. If a subtask is complex enough (e.g., full supply chain modeling), that can be a sub-agent — but it still reports back to the orchestrator.

**Why confidence scores matter:**
Every finding should carry a confidence tag (HIGH/MEDIUM/LOW) based on data freshness, source agreement, and completeness of the entity graph. An analyst needs to know whether the ownership chain is fully resolved or if there are opaque jurisdictions (BVI, Labuan, etc.) where the graph goes dark.

-----

## What You’d Need to Build

|Component                                       |Effort     |Notes                                                   |
|------------------------------------------------|-----------|--------------------------------------------------------|
|Orchestrator agent (Claude + prompt engineering)|Medium     |Core reasoning layer, scenario decomposition logic      |
|Sayari MCP tool                                 |Medium     |Entity resolution, graph traversal, UBO extraction      |
|Kharon/Sanctions MCP tool                       |Low-Medium |Watchlist matching, proximity scoring                   |
|Market Data MCP tool                            |Medium     |Refinitiv/Bloomberg API wrapper, exposure calculations  |
|Trade/Shipping MCP tool                         |Medium-High|Depends on data access; AIS data is messy               |
|Output renderer (report + structured)           |Medium     |Templated reports, JSON schema for downstream           |
|Evaluation harness                              |Medium     |Ground-truth scenarios to test accuracy and completeness|

-----

## Next Steps


**Pick a reference scenario** — one real-world sanction case with known outcomes to validate the workflow end-to-end
**Stand up the Sayari MCP tool first** — corporate graph is the backbone; everything else decorates it
**Build the orchestrator prompt** — scenario classification, decomposition templates, synthesis instructions
**Add data sources incrementally** — each new MCP tool expands the question types you can handle
**Test with analysts** — put it in front of real users early; their questions will be weirder and better than anything we design for
[2:41 PM]Want to build out a rough MVP roughly along these lines. Need to build a very simple demo-able POC for a quick-turnaround potential win with a current customer.
[2:41 PM]Some additional datasets i want to look at: ACLED (Armed Conflict Location & Event Data) — Cost: free with registration; no public charge for standard access. Capabilities: near-real-time global political violence, protest, riot, and strategic-development event data; event dates, locations, actors, fatalities, event types/subtypes, maps/exports, Trendfinder, Conflict Index, and country/region monitoring tools. Best for event-level conflict intensity, escalation tracking, protest monitoring, and building daily conflict features for prediction.
UCDP (Uppsala Conflict Data Program) — Cost: free; datasets are explicitly free of charge under CC BY 4.0. Capabilities: organized violence datasets, battle-related deaths, georeferenced event data, conflict dyads, non-state conflict, one-sided violence, peacemaking data, codebooks, and historical conflict severity/structure data. Best for cleaner historical labels and long-run supervised learning targets.
GSDB (Global Sanctions Data Base) — Cost: public dataset; no public price listed. Capabilities: historical sanctions cases from 1950–2023; bilateral, multilateral, and plurilateral sanctions; sanction type, sender/target, political objective, and outcome/success coding. Best for historical sanctions-event analysis and policy-response modeling.
OFAC Sanctions List Service / OFAC sanctions data — Cost: free. Capabilities: current SDN and consolidated sanctions lists, ready-to-download sanctions data, sanctions-list search, recent designations/actions, and official U.S. sanctions screening data. Best for live sanctions ingestion and compliance-grade designation updates.
FRED / ALFRED — Cost: free; API access available with a free account/API key. Capabilities: economic and financial time series by source, release, category, and series; API retrieval; dashboards; saved lists/graphs; vintage/revision-aware data through ALFRED. Useful for VIX, rates, macro, FX proxies, commodity-linked macro series, and point-in-time modeling.
UN Comtrade — Cost: free registration for previews plus a free API key tier with up to 100k records per call and 500 API calls/day; also offers more powerful API options. Capabilities: detailed global annual and monthly merchandise trade statistics, reporter/partner-country flows, product-level trade, developer portal/API, bulk trade-flow extraction. Best for sanctions spillover, trade rerouting, bilateral exposure, and product-level import/export dependence.
EIA Open Data / EIA API — Cost: free; registration/API key required. Capabilities: U.S. energy data via API, including petroleum prices, crude and product spot prices, weekly/monthly/annual series, inventories, production, imports/exports, and related energy statistics. Best for oil and energy-market response modeling.
World Bank Pink Sheet / Commodity Markets — Cost: free. Capabilities: monthly and annual historical commodity prices, downloadable XLSX/PDF releases, energy/non-energy/food/fertilizer/metals coverage, commodity market commentary and index updates. Best for long-run commodity shock studies.
IMF Data APIs / IMF Data Portal — Cost: free. Capabilities: SDMX APIs, downloadable macroeconomic and financial datasets, CPI, external accounts, commodity prices, reserves, WEO-style macro access, and a data portal/API framework for IMF datasets. Best for country stress, inflation, reserves, and macro deterioration after sanctions or war.
MarineCadastre AIS / Vessel Traffic — Cost: free for public use. Capabilities: U.S. Coast Guard-derived AIS data, vessel traffic downloads, user-defined geography/time-period extraction, national viewer/map system, U.S. coastal and offshore vessel movement data. Best for prototyping maritime disruption analytics, though it is U.S.-focused rather than truly global.
UNCTADstat / UNCTAD maritime indicators — Cost: free. Capabilities: Liner Shipping Connectivity Index (LSCI), bilateral connectivity, maritime transport indicators, ship calls/capacity/services/companies-based connectivity measures, and broader trade/transport statistics. Best for measuring structural shipping connectivity and logistics vulnerability rather than live vessel movements.
CFTC Commitment of Traders (COT) — Cost: free. Capabilities: weekly COT reports, historical annual compressed files, futures-only and futures-and-options reports, disaggregated and financial futures positioning, open-interest breakdowns by trader type. Best for understanding speculative and commercial positioning around oil and other futures during geopolitical stress.
GDELT 2.0 — Cost: free and open. Capabilities: global news-derived event database, near-real-time updates every 15 minutes, multilingual translation, event extraction, themes, tone/emotion measures, analysis service, BigQuery access, DOC/GEO APIs, and global media monitoring. Best for attention, tone, narrative heat, and weak-signal event detection; worse as a clean ground-truth source because it is noisy.
Correlates of War (COW) — Cost: free public release. Capabilities: interstate and civil war datasets, militarized interstate disputes, alliances, direct contiguity, national material capabilities, IGOs, territorial change, and long-run state-system data back to 1816. Best for deep historical structural modeling rather than live monitoring.
SIPRI Arms Transfers Database — Cost: free. Capabilities: major conventional arms transfers since 1950, transfer registers, trend-indicator values, supplier/recipient relationships, coverage of sales, gifts, leases, and licenses under SIPRI methodology. Best for security assistance, arms-flow dependence, and conflict-escalation context.
FactSet — Cost: no public standard pricing; enterprise quote-based and generally expensive. Capabilities: broad institutional financial data platform; global prices, volume, turnover, VWAP, delayed/end-of-day/realtime quotes, reference data, screening/formula APIs, marketplace datasets, and specific sanctions content such as FactSet Global Sanctions. Best as the premium market-data backbone if you need cross-asset depth and reliable enterprise APIs.
Datalastic — Cost: publicly posted. Starter is €199/month monthly or €179/month billed annually with 20,000 credits/month; Experimenter is €569/month or €512/month billed annually with 80,000 credits/month; Developer Pro+ is €679/month or €611/month billed annually with unlimited credits; it also advertises short trial offers. Capabilities: global AIS vessel and port API, real-time and historical vessel tracking, location tracking, port data, port terminals, vessel finder, port finder, ship specs, and developer-friendly maritime API access. Best value commercial maritime API in this list.
Sayari — Cost: no official public standard pricing; quote-based enterprise sales. Capabilities: global corporate and trade data, beneficial ownership and control relationships, cross-border entity resolution, supplier/commercial network mapping, trade compliance, investigations, enhanced due diligence, risk identification, and graph-based analysis of corporate/trade networks. Best for uncovering hidden ownership, sanctions-evasion networks, intermediary exposure, and supply-chain risk.
Kpler — Cost: no public standard pricing; quote/demo based. Capabilities: real-time trade intelligence, 40+ commodity markets, power markets, maritime logistics, AIS vessel tracking, cargo analytics, physical-market intelligence, technical vessel specs, ownership/management/classification/insurer records, APIs, and commodity intelligence with price/fundamentals overlays. Best boutique option for combining maritime flows with commodity-market intelligence, especially oil/LNG/tanker analytics.
WITS (World Integrated Trade Solution) — Cost: the software itself is free, but some underlying databases can have access restrictions or fees depending on user status; WITS itself is not fee-charging. Capabilities: merchandise trade data, tariff data, non-tariff measure data, country profiles, custom analysis, tariff-cut simulation, trade competitiveness analysis, and access to sources such as UN Comtrade, UNCTAD TRAINS, and WTO databases through one interface. Best as an analytical trade workbench rather than a pure raw-data pipe.