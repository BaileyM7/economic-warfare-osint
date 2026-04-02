"""Registry that maps tool names to actual MCP tool implementations.

For the demo, tools are called directly (in-process) rather than via MCP transport.
This keeps the demo simple — no need to run separate MCP server processes.
The architecture still follows MCP conventions so it can be upgraded to real MCP later.
"""

from __future__ import annotations

from typing import Any


class ToolRegistry:
    """Maps tool names to their implementations across all tool modules."""

    def __init__(self) -> None:
        self._tools: dict[str, Any] = {}
        self._loaded = False

    async def _ensure_loaded(self) -> None:
        """Lazy-load all tool implementations."""
        if self._loaded:
            return

        # Import tool modules — each exposes async functions
        try:
            from src.tools.sanctions.server import (
                check_sanctions_status,
                get_recent_designations,
                get_sanctions_proximity,
                search_sanctions,
            )
            self._tools["search_sanctions"] = search_sanctions
            self._tools["check_sanctions_status"] = check_sanctions_status
            self._tools["get_sanctions_proximity"] = get_sanctions_proximity
            self._tools["get_recent_designations"] = get_recent_designations
        except ImportError as e:
            print(f"  Warning: sanctions tools not available: {e}")

        try:
            from src.tools.corporate.server import (
                get_beneficial_owners,
                get_corporate_tree,
                get_offshore_connections,
                resolve_entity,
                search_entity,
            )
            self._tools["search_entity"] = search_entity
            self._tools["get_corporate_tree"] = get_corporate_tree
            self._tools["get_beneficial_owners"] = get_beneficial_owners
            self._tools["get_offshore_connections"] = get_offshore_connections
            self._tools["resolve_entity"] = resolve_entity
        except ImportError as e:
            print(f"  Warning: corporate tools not available: {e}")

        try:
            from src.tools.market.server import (
                get_institutional_holders,
                get_macro_indicator,
                get_market_exposure,
                get_price_history,
                get_stock_profile,
                search_market_entity,
            )
            self._tools["get_stock_profile"] = get_stock_profile
            self._tools["get_price_history"] = get_price_history
            self._tools["get_institutional_holders"] = get_institutional_holders
            self._tools["get_market_exposure"] = get_market_exposure
            self._tools["get_macro_indicator"] = get_macro_indicator
            self._tools["search_market_entity"] = search_market_entity
        except ImportError as e:
            print(f"  Warning: market tools not available: {e}")

        try:
            from src.tools.trade.server import (
                get_bilateral_trade,
                get_commodity_trade,
                get_shipping_connectivity,
                get_supply_chain_exposure,
                get_trade_partners,
            )
            self._tools["get_bilateral_trade"] = get_bilateral_trade
            self._tools["get_commodity_trade"] = get_commodity_trade
            self._tools["get_supply_chain_exposure"] = get_supply_chain_exposure
            self._tools["get_trade_partners"] = get_trade_partners
            self._tools["get_shipping_connectivity"] = get_shipping_connectivity
        except ImportError as e:
            print(f"  Warning: trade tools not available: {e}")

        try:
            from src.tools.geopolitical.server import (
                get_bilateral_tensions,
                get_conflict_data,
                get_event_timeline,
                get_risk_profile,
                search_events,
            )
            self._tools["search_events"] = search_events
            self._tools["get_conflict_data"] = get_conflict_data
            self._tools["get_risk_profile"] = get_risk_profile
            self._tools["get_bilateral_tensions"] = get_bilateral_tensions
            self._tools["get_event_timeline"] = get_event_timeline
        except ImportError as e:
            print(f"  Warning: geopolitical tools not available: {e}")

        try:
            from src.tools.economic.server import (
                estimate_sanction_impact,
                get_commodity_prices,
                get_country_profile,
                get_gdp_exposure,
                get_macro_series,
            )
            self._tools["get_country_profile"] = get_country_profile
            self._tools["get_gdp_exposure"] = get_gdp_exposure
            self._tools["get_commodity_prices"] = get_commodity_prices
            self._tools["get_macro_series"] = get_macro_series
            self._tools["estimate_sanction_impact"] = estimate_sanction_impact
        except ImportError as e:
            print(f"  Warning: economic tools not available: {e}")

        try:
            from src.tools.sayari.server import (
                sayari_get_entity,
                sayari_get_related,
                sayari_get_ubo,
                sayari_resolve,
            )
            self._tools["sayari_resolve"] = sayari_resolve
            self._tools["sayari_get_related"] = sayari_get_related
            self._tools["sayari_get_ubo"] = sayari_get_ubo
            self._tools["sayari_get_entity"] = sayari_get_entity
        except ImportError as e:
            print(f"  Warning: sayari tools not available: {e}")

        self._loaded = True
        print(f"  Loaded {len(self._tools)} tools")

    async def call_tool(self, name: str, params: dict[str, Any]) -> Any:
        """Call a tool by name with the given parameters."""
        await self._ensure_loaded()

        if name not in self._tools:
            return {"error": f"Unknown tool: {name}", "available": list(self._tools.keys())}

        fn = self._tools[name]
        try:
            result = await fn(**params)
            # If it returns a ToolResponse, serialize it
            if hasattr(result, "model_dump"):
                return result.model_dump(mode="json")
            return result
        except TypeError as e:
            # Parameter mismatch — try common positional arg patterns
            # LLM often uses "company", "name", "entity", "symbol" instead of "ticker"/"query"
            for key in ("query", "entity_name", "ticker", "country", "name",
                        "company", "entity", "symbol", "commodity_code"):
                if key in params:
                    try:
                        result = await fn(params[key])
                        if hasattr(result, "model_dump"):
                            return result.model_dump(mode="json")
                        return result
                    except Exception:
                        continue
            # Last resort: try passing first value as positional arg
            if params:
                first_val = next(iter(params.values()))
                try:
                    result = await fn(first_val)
                    if hasattr(result, "model_dump"):
                        return result.model_dump(mode="json")
                    return result
                except Exception:
                    pass
            return {"error": f"Tool call failed: {e}"}

    def list_tools(self) -> list[str]:
        """Return names of all registered tools."""
        return list(self._tools.keys())
