# 04 — Tools & Data Sources

Layer 2 (`src/tools/`) wraps every external data source behind a uniform envelope. This doc is
the reference for *what data comes from where, and what key it needs*. It supersedes the older
`plans/TOOL_REFERENCE.md`.

## The tool pattern

Convention (per [src/tools/CLAUDE.md](../src/tools/CLAUDE.md)):

```
src/tools/<domain>/
  server.py   # MCP tool functions (FastMCP @mcp.tool()), the orchestrator-facing surface
  client.py   # raw API client(s) for the underlying source(s)
  models.py   # Pydantic request/response shapes
```

Every tool function returns a **`ToolResponse`** (`data`, `confidence`, `sources`, `timestamp`,
`errors`) and caches via `src/common/cache.py`. **The convention is not uniform** — `vessels/`,
`markets/`, and `screening/` deviate (see the table); that's tracked in [08](08-fragility-map.md).

### Two consumers of these tools
1. **The orchestrator** dispatches by name through `ToolRegistry` ([tool_registry.py](../src/orchestrator/tool_registry.py)),
   which lazy-imports each `server.py`.
2. **Dashboard/search endpoints in `api.py`** import the **clients directly** (e.g.
   `from src.tools.market.client import YFinanceClient`). So a change to a client signature can
   break both the agent path and several endpoints at once.

## Data-source matrix

| Domain (`src/tools/…`) | Source | Env key | Free? | Used for |
|------------------------|--------|---------|-------|----------|
| `sanctions/` | **OpenSanctions** | `OPENSANCTIONS_API_KEY` (optional) | ✅ | Unified intl. sanctions; topics → hit score |
| `sanctions/` | **OFAC SDN** | none | ✅ | US Treasury SDN list (CSV, cached) |
| `screening/` | **CSL** (Trade.gov) | none | ✅ | Aggregates OFAC + **BIS Entity List** + EU + UK + UN |
| `corporate/` | **OpenCorporates** | `OPENCORPORATES_API_KEY` (optional) | ✅ | Company + officer lookup |
| `corporate/` | **GLEIF** | none | ✅ | LEI registry, parent/subsidiary |
| `corporate/` | **ICIJ Offshore Leaks** | none | ✅ | Panama/Pandora Papers entities |
| `market/` | **yfinance** (Yahoo) | none | ✅ | Quotes, profiles, holders (blocked on some cloud IPs) |
| `market/` | **Finnhub** | `FINNHUB_API_KEY` (optional) | ✅ | **Fallback** when yfinance's WAF blocks cloud IPs |
| `market/` | **SEC EDGAR** | none (UA header) | ✅ | 13F filings, insider txns |
| `trade/` | **UN Comtrade** | `COMTRADE_API_KEY` (optional, 500/day) | ✅ | Bilateral trade flows |
| `trade/` | **UNCTADstat** | none | ✅ | Trade/supply-chain stats |
| `geopolitical/` | **GDELT 2.0** | none | ✅ | Global news events, tone |
| `geopolitical/` | **ACLED** | `ACLED_API_KEY` + `ACLED_EMAIL` (+ `ACLED_PASSWORD`) | ✅ (registration) | Conflict/protest events |
| `economic/` | **FRED** | `FRED_API_KEY` (optional) | ✅ | Macro indicators, VIX, rates |
| `economic/` | **IMF / World Bank** | none | ✅ | Macro series |
| `economic/` | **EIA Open Data** | (no key per `.env.example`) | ✅ | Energy data |
| `vessels/` | **AISStream.io** | `AISSTREAM_API_KEY` (optional) | ✅ | Global AIS firehose (WebSocket) |
| `vessels/` | **OpenSanctions vessel schema** + **`fixtures/vessels.json`** | none | ✅ | Vessel particulars; **fixture wins over OpenSanctions** (commit #19) |
| `sayari/` | **Sayari Graph** | `SAYARI_CLIENT_ID` + `SAYARI_CLIENT_SECRET` (or `SAYARI_API_KEY`) | 💰 premium | Entity resolution, UBO, traversal |
| `graph/` | **Emissary knowledge store** (local SQLite) | none | ✅ | Issue #31 — agent tools to *work on the graph*: save entities/edges, list, neighbors, find exposure paths. All go through the `src/common/knowledge_store.py` seam shared with `/api/knowledge/*` |
| *(person path)* | **Wikidata** | none | ✅ | PEP (politically-exposed person) lookups |

> **`market/` vs `markets/`** — these are different. `market/` is the equity-data **tool**
> (`YFinanceClient`, `FinnhubClient`, `SECEdgarClient`). `markets/` is a single `feed.py`
> (`build_markets_feed`) that produces the **risk-feed markets card**. The name collision is a
> sharp edge — import the right one. See [08](08-fragility-map.md).

## Caching (`src/common/cache.py`)

- Backend: **`diskcache`**, keyed by a SHA256 of the call params.
- TTL: `CACHE_TTL_SECONDS` (default **3600s**); directory `CACHE_DIR` (default `data/cache/`).
- Purpose: stay under free-tier rate limits and keep demos fast/deterministic. Some sources use
  longer effective TTLs (e.g. the OFAC SDN list is treated as daily).
- Clearing: delete `data/cache/`, or use the stray helper `clear_peers_cache.py` (peers cache only).

## HTTP + sanitization

- `src/common/http_client.py` — async `fetch_json` / `fetch_text` with retry/backoff; the single
  place outbound HTTP should go.
- `src/common/sanitize.py` — sanitizes user text before it reaches Claude prompts (prompt-injection hygiene).

## Adding a data source (checklist)

1. Add/extend a `client.py` in the right `src/tools/<domain>/` (or create a new domain following
   the `server/client/models` pattern).
2. Return a `ToolResponse` with real `sources` (provenance) and a `confidence`.
3. Cache via `src/common/cache.py`; route HTTP through `src/common/http_client.py`.
4. If the orchestrator should use it, expose it from `server.py` so `ToolRegistry` picks it up.
5. If an endpoint uses it directly, import the **client**, not the server.
6. Add the env key to `.env.example`, to the matrix above, and to the env matrix in
   [06-deployment-render.md](06-deployment-render.md).
7. Add a VCR-cassette test (see [05-ci-cd.md](05-ci-cd.md)) so CI can replay it without live calls.
