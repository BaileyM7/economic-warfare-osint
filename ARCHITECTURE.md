# Architecture Deep Dive — Economic Warfare OSINT

## System Purpose

A multi-agent OSINT system for economic warfare exercise support. Analysts ask natural-language questions about sanctions, supply chain disruption, investment interception, and facility denial. The system autonomously gathers data from 15+ free public sources and returns a structured `ImpactAssessment` with entity graphs, confidence scores, and friendly-fire alerts.

---

## Three-Layer Architecture

```
┌──────────────────────────────────────────────────────────┐
│  LAYER 1: Orchestrator Agent                              │
│  src/orchestrator/main.py — Orchestrator.analyze()        │
│  • Decomposes question → research plan (DAG of steps)     │
│  • Dispatches tool calls                                  │
│  • Synthesizes results → ImpactAssessment                 │
└────────────────────────┬─────────────────────────────────┘
                         │  ToolRegistry.call_tool()
                         │  src/orchestrator/tool_registry.py
                         ▼
┌──────────────────────────────────────────────────────────┐
│  LAYER 2: MCP Tool Agents (6 domains, 30 tools)           │
│  src/tools/{sanctions,corporate,market,trade,             │
│             geopolitical,economic}/server.py              │
│  • Each tool is an async function returning ToolResponse  │
│  • Caches API responses locally (diskcache)               │
│  • Normalizes raw data to typed Pydantic models           │
└────────────────────────┬─────────────────────────────────┘
                         │
                         ▼
┌──────────────────────────────────────────────────────────┐
│  LAYER 3: Fusion & Output Engine                          │
│  src/fusion/graph_builder.py + renderer.py                │
│  • Extracts entity/relationship graph from tool results   │
│  • Renders markdown report, JSON, vis.js network graph    │
└──────────────────────────────────────────────────────────┘
```

---

## Request Lifecycle

```
POST /api/analyze  {"query": "What if we sanction Fujian Jinhua?"}
         │
         ▼
src/api.py → spawns background asyncio task
         │
         ▼
Orchestrator.analyze(query)                         [main.py:44]
  │
  ├─ _decompose(query)                              [main.py:72]
  │    Claude call (DECOMPOSITION_PROMPT) →
  │    returns JSON array of research steps:
  │    [{"step":1, "tools":[...], "depends_on":[]}, ...]
  │
  ├─ _execute_plan(plan)                            [main.py:96]
  │    Topological sort by depends_on →
  │    asyncio.gather(*ready_steps) each wave →
  │    ToolRegistry.call_tool(name, params) per tool →
  │    returns dict[str, Any] (step_N → tool results)
  │
  └─ _synthesize(query, tool_results)              [main.py:158]
       Claude call (SYNTHESIS_PROMPT) →
       returns ImpactAssessment JSON →
       parsed into ImpactAssessment Pydantic model
         │
         ▼
render_markdown(assessment)  +  render_entity_graph(graph)
         │
         ▼
GET /api/analyze/{id}  → {status, result, markdown, graph_data}
```

---

## Key Files

| File | Purpose |
|------|---------|
| `src/api.py` | FastAPI app, all HTTP + WebSocket endpoints |
| `src/orchestrator/main.py` | `Orchestrator` class — 4-step pipeline |
| `src/orchestrator/prompts.py` | `SYSTEM_PROMPT`, `DECOMPOSITION_PROMPT`, `SYNTHESIS_PROMPT` |
| `src/orchestrator/tool_registry.py` | Lazy-loads all tool functions; dispatches by name |
| `src/orchestrator/entity_resolver.py` | Classifies query subject: company / person / sector / vessel |
| `src/common/types.py` | All shared Pydantic models (canonical source of truth) |
| `src/common/config.py` | `.env`-based config; validates required keys on startup |
| `src/common/cache.py` | `get_cached()` / `set_cached()` — SHA256-keyed diskcache |
| `src/common/http_client.py` | `fetch_json()`, `post_json()` with retry + backoff |
| `src/tools/*/server.py` | `@mcp.tool()` decorated async tool functions |
| `src/tools/*/client.py` | Raw API clients for underlying data sources |
| `src/tools/*/models.py` | Domain-specific Pydantic models |
| `src/fusion/graph_builder.py` | Extracts `EntityGraph` from tool results |
| `src/fusion/renderer.py` | Renders assessment to markdown / JSON / vis.js |
| `src/sanctions_impact.py` | Deterministic stock-price impact projector (no LLM) |

---

## Data Model (`src/common/types.py`)

```
Confidence              HIGH | MEDIUM | LOW

SourceReference
  name: str             "OpenSanctions", "OFAC SDN", etc.
  url: str | None
  accessed_at: datetime
  dataset_version: str | None

ToolResponse            ← every tool returns this shape
  data: Any             tool-specific payload (typed)
  confidence: Confidence
  sources: list[SourceReference]
  timestamp: datetime
  errors: list[str]

Entity
  id: str
  name: str
  entity_type: str      "company" | "person" | "vessel" | "government"
  aliases: list[str]
  country: str | None
  identifiers: dict     {"lei": "...", "ofac_id": "..."}

Relationship
  source_id: str
  target_id: str
  relationship_type: str  "subsidiary_of" | "beneficial_owner" | "supplies" | ...
  properties: dict
  confidence: Confidence
  sources: list[SourceReference]

EntityGraph
  entities: list[Entity]
  relationships: list[Relationship]
  .add_entity()  .add_relationship()  .merge()

ScenarioType
  SANCTION_IMPACT | SUPPLY_CHAIN_DISRUPTION | INVESTMENT_INTERCEPTION
  | FACILITY_DENIAL | TRADE_DISRUPTION

AnalystQuery
  raw_query: str
  scenario_type: ScenarioType | None
  target_entities: list[str]
  parameters: dict

ImpactAssessment        ← final output of the pipeline
  query: AnalystQuery
  scenario_type: ScenarioType
  executive_summary: str
  findings: list[dict]          # [{category, finding, confidence, data}]
  friendly_fire: list[dict]     # [{entity, exposure_type, estimated_impact, details}]
  entity_graph: EntityGraph
  confidence_summary: dict[str, Confidence]
  sources: list[SourceReference]
  recommendations: list[str]
```

---

## Tool Registry & Dispatch (`src/orchestrator/tool_registry.py`)

`ToolRegistry` lazy-loads all 26 tools on first call. No separate MCP process is needed — tools are called in-process as regular async functions. The MCP decorator (`@mcp.tool()`) is there so each server can also be run standalone (`uv run python -m src.tools.<name>.server`).

**Dispatch flow** (`tool_registry.py:125`):
1. `call_tool(name, params)` — look up function in `self._tools`
2. Call `await fn(**params)`
3. If the result has `.model_dump()`, serialize it
4. On `TypeError` (parameter mismatch), try common fallback keys: `query`, `entity_name`, `country`, `ticker`

---

## Orchestrator Prompts (`src/orchestrator/prompts.py`)

Three prompts drive the pipeline:

**`SYSTEM_PROMPT`** — defines agent role, lists all 26 available tools by name + signature, specifies the 8-step analysis process, and mandates the JSON output schema for `ImpactAssessment`.

**`DECOMPOSITION_PROMPT`** — instructs Claude to produce a JSON array of research steps from the analyst's question. Steps include tool names + params and `depends_on` arrays for DAG execution.

**`SYNTHESIS_PROMPT`** — instructs Claude to synthesize collected tool results into the final `ImpactAssessment` JSON, including explicit friendly-fire assessment even when exposure is minimal.

---

## Caching Strategy (`src/common/cache.py`)

All API calls check the local diskcache before hitting the network:
- **Cache key**: SHA256 of `(namespace + sorted params)`
- **Default TTL**: 3600 s (1 hour) for search results
- **SDN list TTL**: 86400 s (24 hours)
- **Purpose**: Respect free-tier rate limits (UN Comtrade: 500 calls/day; Datalastic: per-credit)

---

## Parallelization

Research plan steps are executed in topological waves based on `depends_on`:

```python
# _execute_plan: main.py:96
while incomplete:
    ready = [s for s in plan if deps satisfied]
    await asyncio.gather(*[_execute_step(s) for s in ready])
```

Independent steps across domains (sanctions + corporate + market) run concurrently, minimizing wall-clock latency despite 6+ API calls per query.

---

## Confidence Scoring

Each tool assigns confidence based on domain-specific heuristics:

| Signal | Effect |
|--------|--------|
| Multiple sources agree | → HIGH |
| Top match score ≥ 0.9 | → HIGH |
| Top match score 0.6–0.9 | → MEDIUM |
| No matches found | → LOW |
| Graph traversal < 3 nodes | → LOW |
| OFAC date extraction (best-effort) | → MEDIUM at most |

The orchestrator synthesizes per-tool confidence into a `confidence_summary` dict on the final `ImpactAssessment`.

---

## Friendly Fire Pattern

Every scenario automatically assesses US/allied exposure. The `SYNTHESIS_PROMPT` explicitly instructs Claude to include a `friendly_fire` array even when exposure is minimal (explicitly stating "no significant exposure" is itself useful signal). Categories:

- US/allied institutional investors holding equity
- Allied companies in the target's supply chain
- Dual-use technology dependencies
- Port/logistics disruptions affecting allied shipping lanes

---

## API Surface (`src/api.py`)

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/` | Dark-themed SPA (vis.js graph + query input) |
| `GET` | `/api/health` | Config validation + model info |
| `GET` | `/api/tools` | List all registered tool names |
| `POST` | `/api/analyze` | Start async analysis → `{analysis_id}` |
| `GET` | `/api/analyze/{id}` | Poll status/results |
| `POST` | `/api/analyze/sync` | Synchronous analysis (blocks) |
| `WS` | `/ws/analyze/{id}` | Live progress via WebSocket |
| `POST` | `/api/sanctions-impact` | Deterministic stock-price impact (no LLM) |

---

## Deterministic Demo Mode (`src/sanctions_impact.py`)

`run_sanctions_impact(ticker)` projects stock-price impact using a reference dataset of 13 historical sanction cases (ZTE, Alibaba, Xiaomi, etc.). No LLM, no API key. Selects comparables by sector + sanction type and models impact windows at 30 / 60 / 90 days. Used for `POST /api/sanctions-impact` — fast, reproducible, safe for demos.

---

## How to Add a New Tool Domain

1. Create `src/tools/<name>/` with `__init__.py`, `server.py`, `client.py`, `models.py`
2. In `server.py`: `mcp = FastMCP("<name>")` and decorate functions with `@mcp.tool()`
3. Each function must return `ToolResponse.model_dump(mode="json")`
4. Register all functions in `ToolRegistry._ensure_loaded()` (`tool_registry.py:20`)
5. Add tool names + signatures to `SYSTEM_PROMPT` in `prompts.py`

---

## How to Extend Scenario Types

1. Add a new value to `ScenarioType` enum in `src/common/types.py`
2. Update `SYSTEM_PROMPT` to describe the new scenario and relevant tools
3. The orchestrator will handle decomposition and synthesis automatically via Claude

---

## Configuration (`.env`)

```
ANTHROPIC_API_KEY=...       # Required
FRED_API_KEY=...            # Optional (free)
COMTRADE_API_KEY=...        # Optional (free, 500 calls/day)
ACLED_API_KEY=...           # Optional (free with registration)
ACLED_EMAIL=...             # Required if ACLED_API_KEY set
ACLED_PASSWORD=...          # Required for ACLED token refresh
REFRESH_TOKEN=...           # ACLED OAuth refresh token
OPENCORPORATES_API_KEY=...  # Optional (improves rate limits)
OPENSANCTIONS_API_KEY=...   # Optional (higher rate limits)
TRADE_GOV_API_KEY=...       # Optional (Trade.gov CSL)
DATALASTIC_API_KEY=...      # Optional (vessel tracking)

CLAUDE_MODEL=claude-sonnet-4-6   # Recommended — defaults to claude-sonnet-4-20250514 if unset
CACHE_TTL_SECONDS=3600
CACHE_DIR=data/cache
```

`src/common/config.py` validates on startup. Missing optional keys degrade gracefully (tools return empty results rather than crashing).

---

## Running Locally

```bash
uv sync
cp .env.example .env       # add ANTHROPIC_API_KEY at minimum
uv run uvicorn src.api:app --reload --host 0.0.0.0 --port 8000
# Opens http://localhost:8000 automatically

# CLI mode
uv run python -m src.orchestrator.main "What if we sanction Huawei?"

# Run a single tool server
uv run python -m src.tools.sanctions.server

# Tests
uv run --extra dev pytest
```
