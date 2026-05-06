# src/ — Source Code

All application code lives here. Each subdirectory is a Python package.

## Package Layout
- `orchestrator/` — Layer 1: the "quarterback" agent that decomposes questions and synthesizes results
- `tools/` — Layer 2: MCP tool agents, one per data domain (see [tools/CLAUDE.md](tools/CLAUDE.md))
- `routers/` — FastAPI HTTP endpoints (see [routers/CLAUDE.md](routers/CLAUDE.md))
- `fusion/` — Layer 3: output rendering (reports, structured data, graph export)
- `common/` — Cross-cutting concerns: types, config, cache, rate limit, sanitize (see [common/CLAUDE.md](common/CLAUDE.md))
- `wargame_backend/` — Embedded swarm subapp (only mounted when `WARGAME_ENABLED=1`)
- `wargame_ai/` — Wargame agent runner (LangGraph-based)
- `wargame_shared/` — Schemas shared between wargame_backend and the frontend
- `api.py` — FastAPI app entry point. Registers middleware (rate limiter, CORS, usage tracking), includes routers, mounts the wargame subapp.

## Conventions
- Each MCP tool is a standalone MCP server that can be tested independently
- All inter-layer communication uses Pydantic models from `common/types.py`
- Every tool function returns a `ToolResponse` envelope with data + confidence + sources
- Config is loaded from environment variables (dotenv) via `common/config.py`
- Every router endpoint that calls Anthropic uses `@limiter.limit(LLM_GENERATE_LIMIT)` + `sanitize_for_llm(...)` — see [routers/CLAUDE.md](routers/CLAUDE.md) for the full pattern
- SQL is always parameterized via `?` placeholders. Never f-string user input into a query

## Layered dependency rule
Imports flow ONE direction only:

```
common/  ←  tools/  ←  orchestrator/  ←  routers/  ←  api.py
                                                  ←  fusion/
```

`common/` imports nothing from the project. `routers/` and `fusion/` are at the
top — they can import from anywhere below. Adding an import that goes the
wrong direction (e.g., `common/` importing from `routers/`) creates a circular
import and a broken app at startup.

## When the app fails to import
Most common cause: a router imports a name that doesn't exist in the module
it's importing from (the bug PR #2 fixed). The fastest diagnosis is:

```bash
python -c "from src.api import app; print('OK')"
```

If this errors, the same error will hit Render at startup. Fix before pushing.
