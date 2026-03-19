# src/tools/ — MCP Tool Agents (Layer 2)

Each subdirectory is an independent MCP tool server wrapping one or more data sources.

## Tool Structure Pattern
Every tool follows the same pattern:
```
tools/<name>/
  __init__.py
  server.py      # MCP server definition with tool functions
  client.py      # API client(s) for the underlying data source(s)
  models.py      # Pydantic models for this tool's request/response shapes
```

## Tool Inventory
| Directory      | Sources                              | Free? |
|---------------|--------------------------------------|-------|
| sanctions/    | OpenSanctions, OFAC SDN              | Yes   |
| corporate/    | OpenCorporates, GLEIF, ICIJ          | Yes   |
| market/       | yfinance, SEC EDGAR, FRED            | Yes   |
| trade/        | UN Comtrade, UNCTADstat              | Yes   |
| geopolitical/ | GDELT 2.0, ACLED                     | Yes   |
| economic/     | FRED, IMF, World Bank                | Yes   |

## MCP Server Pattern
Each server.py exposes tools via the MCP Python SDK:
```python
from mcp.server.fastmcp import FastMCP
mcp = FastMCP("tool-name")

@mcp.tool()
async def search_entity(query: str) -> ToolResponse:
    ...
```

## Key Rules
- Always return `ToolResponse` envelope (see common/types.py)
- Include confidence score based on data completeness/freshness
- Include source provenance (URL, dataset name, access timestamp)
- Cache API responses locally to respect rate limits on free tiers
- Never return raw HTML or unstructured text — always normalize to typed JSON
