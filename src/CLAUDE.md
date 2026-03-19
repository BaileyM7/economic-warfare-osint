# src/ — Source Code

All application code lives here. Each subdirectory is a Python package.

## Package Layout
- `orchestrator/` — Layer 1: the "quarterback" agent that decomposes questions and synthesizes results
- `tools/` — Layer 2: MCP tool agents, one per data domain
- `fusion/` — Layer 3: output rendering (reports, structured data, graph export)
- `common/` — Shared types (Pydantic models), config loading, caching, base client

## Conventions
- Each MCP tool is a standalone MCP server that can be tested independently
- All inter-layer communication uses Pydantic models from `common/types.py`
- Every tool function returns a `ToolResponse` envelope with data + confidence + sources
- Config is loaded from environment variables (dotenv) via `common/config.py`
