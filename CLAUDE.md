# Economic Warfare OSINT Agentic Workflow

## Project Overview
Multi-agent OSINT system for economic warfare exercise support. Takes natural language questions about sanctions, supply chain disruption, and investment interception, then autonomously gathers data from free/commercial sources and produces structured impact assessments.

## Architecture
- **Three-layer model**: Orchestrator Agent → MCP Tool Agents → Fusion & Output Engine
- **Orchestrator**: Claude API via Anthropic SDK, handles scenario decomposition and synthesis
- **MCP Tools**: Each wraps a data source, returns typed JSON with confidence scores and provenance
- **Fusion Engine**: Renders structured reports (Markdown/PDF), JSON, and graph exports

## Tech Stack
- **Language**: Python 3.14
- **Package Manager**: uv
- **LLM**: Claude API (Anthropic SDK)
- **MCP Framework**: Python MCP SDK (`mcp`)
- **Data formats**: JSON, Markdown, vis.js-compatible graph data

## Project Structure
```
src/
  orchestrator/     # Layer 1 - main agent logic, scenario decomposition
  tools/            # Layer 2 - MCP tool agents (one per data domain)
    sanctions/      # OpenSanctions + OFAC SDN
    corporate/      # OpenCorporates + GLEIF + ICIJ Offshore Leaks
    market/         # yfinance + SEC EDGAR 13F + FRED
    trade/          # UN Comtrade + UNCTADstat
    geopolitical/   # GDELT + ACLED
    economic/       # FRED + IMF + World Bank
  fusion/           # Layer 3 - report generation, graph export
  common/           # Shared types, config, API client base
tests/
  fixtures/         # Reference scenario data (Fujian Jinhua, etc.)
data/cache/         # Local data cache for API responses
templates/          # Report templates (Markdown/Jinja2)
```

## Data Source Strategy (Demo)
All paid sources (Sayari, Kharon, Refinitiv, Bloomberg, FactSet, Kpler) are substituted with free alternatives:
- **Sanctions**: OpenSanctions.org + OFAC SDN (free)
- **Corporate**: OpenCorporates + GLEIF + ICIJ Offshore Leaks (free)
- **Market**: yfinance + SEC EDGAR + FRED (free)
- **Trade**: UN Comtrade + UNCTADstat (free)
- **Geopolitical**: GDELT 2.0 + ACLED (free with registration)
- **Economic**: FRED + IMF APIs + World Bank (free)

## Key Conventions
- Every MCP tool returns `ToolResponse` with: `data`, `confidence` (HIGH/MEDIUM/LOW), `sources` list, `timestamp`
- Config via `.env` file — see `.env.example` for all keys
- Use `src/common/types.py` for shared Pydantic models
- Use `src/common/cache.py` for API response caching (avoid hammering free tiers)
- All tools are independently testable without the orchestrator

## Commands
- `uv run uvicorn src.api:app --reload --host 0.0.0.0 --port 8000` — start web server (primary)
- `uv run python -m src.orchestrator.main "your question here"` — CLI mode
- `uv run --extra dev pytest` — run tests
- `uv run python -m src.tools.<name>.server` — run individual MCP tool server

## Web API Endpoints
- `GET /` — demo frontend (single-page app, dark themed, vis.js graph)
- `GET /api/health` — health check + config validation
- `GET /api/tools` — list available tools
- `POST /api/analyze` — start async analysis (returns analysis_id)
- `GET /api/analyze/{id}` — poll analysis status/results
- `POST /api/analyze/sync` — synchronous analysis (blocks until done)
- `WS /ws/analyze/{id}` — WebSocket for live progress updates
