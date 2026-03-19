"""MCP server for Economic Modeling tools.

Exposes tools for country economic profiles, GDP exposure analysis,
commodity prices, macro time series, and sanctions impact estimation.

Run standalone:
    uv run python -m src.tools.economic.server
"""

from __future__ import annotations

import logging
from datetime import datetime

from mcp.server.fastmcp import FastMCP

from src.common.types import Confidence, SourceReference, ToolResponse

from .client import (
    COMMODITY_SERIES,
    FRED_WARFARE_SERIES,
    IMF_INDICATORS,
    WB_INDICATORS,
    FREDClient,
    IMFClient,
    WorldBankClient,
    _resolve_country,
    build_country_profile,
    fetch_macro_series,
)
from .models import (
    CommodityPrice,
    CountryEconomicProfile,
    EconomicIndicator,
    SanctionImpactEstimate,
)

logger = logging.getLogger(__name__)

mcp = FastMCP("economic-modeling")

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_PERIOD_DAYS: dict[str, int] = {
    "1m": 30,
    "3m": 90,
    "6m": 180,
    "1y": 365,
    "2y": 730,
    "5y": 1825,
    "10y": 3650,
}

# Heuristic data for sanctions impact estimation.
# trade_share: approximate share of GDP that is trade-exposed (0-1)
# openness: how open/diversified the economy is (higher = more resilient)
# key_exports: primary export categories
_COUNTRY_HEURISTICS: dict[str, dict] = {
    "RUS": {
        "trade_share": 0.46,
        "openness": 0.35,
        "key_exports": ["Oil & Gas", "Metals", "Weapons", "Agriculture"],
        "energy_dependence": 0.60,
    },
    "CHN": {
        "trade_share": 0.38,
        "openness": 0.50,
        "key_exports": ["Electronics", "Machinery", "Textiles", "Chemicals"],
        "energy_dependence": 0.20,
    },
    "IRN": {
        "trade_share": 0.42,
        "openness": 0.20,
        "key_exports": ["Oil & Gas", "Petrochemicals", "Metals", "Agriculture"],
        "energy_dependence": 0.70,
    },
    "PRK": {
        "trade_share": 0.25,
        "openness": 0.05,
        "key_exports": ["Coal", "Textiles", "Fishery", "Minerals"],
        "energy_dependence": 0.30,
    },
    "VEN": {
        "trade_share": 0.50,
        "openness": 0.15,
        "key_exports": ["Oil & Gas", "Gold", "Chemicals", "Agriculture"],
        "energy_dependence": 0.85,
    },
    "SYR": {
        "trade_share": 0.35,
        "openness": 0.10,
        "key_exports": ["Oil", "Agriculture", "Textiles", "Phosphates"],
        "energy_dependence": 0.50,
    },
    "CUB": {
        "trade_share": 0.40,
        "openness": 0.10,
        "key_exports": ["Sugar", "Tobacco", "Nickel", "Medical Services"],
        "energy_dependence": 0.35,
    },
    "MMR": {
        "trade_share": 0.40,
        "openness": 0.20,
        "key_exports": ["Natural Gas", "Gems", "Agriculture", "Textiles"],
        "energy_dependence": 0.40,
    },
    "TUR": {
        "trade_share": 0.60,
        "openness": 0.50,
        "key_exports": ["Automotive", "Textiles", "Electronics", "Agriculture"],
        "energy_dependence": 0.25,
    },
}

# Sanction type multipliers — how much of trade-exposed GDP is affected
_SANCTION_MULTIPLIERS: dict[str, float] = {
    "comprehensive": 0.80,  # full trade embargo + SWIFT cutoff
    "sectoral": 0.35,  # sector-specific restrictions
    "financial": 0.45,  # SWIFT disconnection, asset freezes, bank sanctions
    "energy": 0.55,  # oil/gas price caps, export bans
    "technology": 0.25,  # export controls on tech/chips
    "individual": 0.05,  # targeted individual/entity sanctions
    "trade": 0.40,  # broad trade restrictions / tariffs
    "arms": 0.10,  # arms embargo only
}


def _estimate_affected_sectors(
    sanction_type: str,
    country_info: dict,
) -> list[str]:
    """Pick which sectors are most affected based on sanction type."""
    exports = country_info.get("key_exports", [])
    energy_dep = country_info.get("energy_dependence", 0.3)

    if sanction_type == "comprehensive":
        return exports  # everything hit
    if sanction_type == "energy":
        return [s for s in exports if any(k in s.lower() for k in ["oil", "gas", "energy", "petro", "coal"])] or exports[:2]
    if sanction_type == "technology":
        return [s for s in exports if any(k in s.lower() for k in ["tech", "electron", "machine", "chip"])] or ["Technology"]
    if sanction_type == "financial":
        return ["Finance", "Banking"] + exports[:2]
    if sanction_type == "trade":
        return exports[:3]
    if sanction_type == "arms":
        return [s for s in exports if any(k in s.lower() for k in ["weapon", "defense", "military"])] or ["Defense"]
    if sanction_type == "sectoral":
        return exports[:2]
    return exports[:2]


# ---------------------------------------------------------------------------
# MCP Tools
# ---------------------------------------------------------------------------

@mcp.tool()
async def get_country_profile(country: str) -> dict:
    """Get an economic snapshot of a country using IMF and World Bank data.

    Args:
        country: Country name (e.g. "China", "Russia") or ISO code (e.g. "CHN", "RU").

    Returns:
        ToolResponse with CountryEconomicProfile data including GDP, inflation,
        unemployment, reserves, debt-to-GDP, FDI inflows, and top sectors.
    """
    errors: list[str] = []
    try:
        profile = await build_country_profile(country)
    except Exception as exc:
        logger.exception("Failed to build country profile for %s", country)
        errors.append(f"Error fetching profile: {exc}")
        profile = CountryEconomicProfile(country=country)

    # Assess confidence based on data completeness
    filled = sum(
        1
        for v in [
            profile.gdp_usd,
            profile.gdp_growth_pct,
            profile.inflation_pct,
            profile.unemployment_pct,
            profile.reserves_usd,
            profile.debt_to_gdp_pct,
            profile.fdi_inflows_usd,
        ]
        if v is not None
    )
    if filled >= 5:
        confidence = Confidence.HIGH
    elif filled >= 3:
        confidence = Confidence.MEDIUM
    else:
        confidence = Confidence.LOW

    return ToolResponse(
        data=profile.model_dump(),
        confidence=confidence,
        sources=[
            SourceReference(
                name="IMF DataMapper",
                url="https://www.imf.org/external/datamapper",
                accessed_at=datetime.utcnow(),
            ),
            SourceReference(
                name="World Bank Open Data",
                url="https://data.worldbank.org",
                accessed_at=datetime.utcnow(),
            ),
        ],
        errors=errors,
    ).model_dump()


@mcp.tool()
async def get_gdp_exposure(country: str, sector: str = "") -> dict:
    """Analyze GDP and sectoral breakdown for a country.

    Args:
        country: Country name or ISO code.
        sector: Optional sector name to focus on (e.g. "Energy", "Technology").

    Returns:
        ToolResponse with GDP data and sector exposure analysis.
    """
    codes = _resolve_country(country)
    iso2 = codes["iso2"]
    imf_code = codes["imf"]
    iso3 = codes["iso3"]
    errors: list[str] = []

    imf = IMFClient()
    wb = WorldBankClient()

    # Fetch GDP
    gdp_val, gdp_year = await imf.get_latest_value("NGDPD", imf_code)
    gdp_usd = gdp_val * 1_000_000_000 if gdp_val else None
    if gdp_usd is None:
        gdp_usd, gdp_year = await wb.get_latest_value(iso2, WB_INDICATORS["gdp"])

    # Fetch trade as % of GDP for exposure calculation
    trade_pct, _ = await wb.get_latest_value(iso2, WB_INDICATORS["trade_pct_gdp"])

    # Fetch exports and imports
    exports, _ = await wb.get_latest_value(iso2, WB_INDICATORS["exports"])
    imports, _ = await wb.get_latest_value(iso2, WB_INDICATORS["imports"])

    # Heuristic sector breakdown
    from .client import _DEFAULT_SECTORS
    sectors = _DEFAULT_SECTORS.get(iso3, ["Services", "Manufacturing", "Agriculture"])

    # Build exposure data
    exposure_data = {
        "country": country,
        "gdp_usd": gdp_usd,
        "gdp_year": gdp_year,
        "trade_as_pct_of_gdp": round(trade_pct, 2) if trade_pct else None,
        "exports_usd": exports,
        "imports_usd": imports,
        "top_sectors": sectors,
    }

    if sector:
        # Check if the sector is in the known list
        sector_lower = sector.lower()
        matched = [s for s in sectors if sector_lower in s.lower()]
        exposure_data["queried_sector"] = sector
        exposure_data["sector_found"] = len(matched) > 0
        exposure_data["sector_matches"] = matched
        # Rough heuristic: divide GDP evenly among top sectors for demo purposes
        if gdp_usd and sectors:
            sector_share = gdp_usd / len(sectors)
            exposure_data["estimated_sector_gdp_usd"] = round(sector_share, 2)
            exposure_data["sector_share_pct"] = round(100.0 / len(sectors), 2)

    confidence = Confidence.MEDIUM if gdp_usd else Confidence.LOW

    return ToolResponse(
        data=exposure_data,
        confidence=confidence,
        sources=[
            SourceReference(
                name="IMF DataMapper",
                url="https://www.imf.org/external/datamapper",
                accessed_at=datetime.utcnow(),
            ),
            SourceReference(
                name="World Bank Open Data",
                url="https://data.worldbank.org",
                accessed_at=datetime.utcnow(),
            ),
        ],
        errors=errors,
    ).model_dump()


@mcp.tool()
async def get_commodity_prices(commodity: str, period: str = "1y") -> dict:
    """Get commodity price time series data.

    Args:
        commodity: Commodity name (e.g. "oil", "gold", "natural gas", "wheat",
                   "copper", "aluminum", "iron ore", "corn", "nickel").
        period: Time period — one of "1m", "3m", "6m", "1y", "2y", "5y".

    Returns:
        ToolResponse with list of CommodityPrice observations.
    """
    errors: list[str] = []
    period_days = _PERIOD_DAYS.get(period, 365)
    fred = FREDClient()

    series_id = COMMODITY_SERIES.get(commodity.lower())
    if not series_id:
        return ToolResponse(
            data={"commodity": commodity, "prices": [], "error": "Unknown commodity"},
            confidence=Confidence.LOW,
            sources=[],
            errors=[f"Commodity '{commodity}' not found. Available: {', '.join(COMMODITY_SERIES.keys())}"],
        ).model_dump()

    try:
        prices = await fred.get_commodity_price(commodity, period_days=period_days)
    except Exception as exc:
        logger.exception("Failed to fetch commodity prices for %s", commodity)
        prices = []
        errors.append(f"FRED API error: {exc}")

    # Summary statistics
    price_values = [p.price for p in prices if p.price is not None]
    summary = {}
    if price_values:
        summary = {
            "latest": price_values[-1],
            "high": max(price_values),
            "low": min(price_values),
            "average": round(sum(price_values) / len(price_values), 2),
            "period_change_pct": round(
                ((price_values[-1] - price_values[0]) / price_values[0]) * 100, 2
            )
            if price_values[0] != 0
            else None,
        }

    return ToolResponse(
        data={
            "commodity": commodity,
            "period": period,
            "observation_count": len(prices),
            "summary": summary,
            "prices": [p.model_dump() for p in prices],
        },
        confidence=Confidence.HIGH if prices else Confidence.LOW,
        sources=[
            SourceReference(
                name="FRED (Federal Reserve Economic Data)",
                url=f"https://fred.stlouisfed.org/series/{series_id}",
                accessed_at=datetime.utcnow(),
            ),
        ],
        errors=errors,
    ).model_dump()


@mcp.tool()
async def get_macro_series(
    indicator: str,
    country: str,
    years: int = 5,
) -> dict:
    """Get a macroeconomic time series for a country.

    Args:
        indicator: Indicator key or code. Human-readable keys:
                   "gdp", "gdp_growth", "inflation", "unemployment",
                   "fdi", "reserves", "debt_to_gdp", "exports", "imports",
                   "current_account", "trade_pct_gdp".
                   Or raw codes like "NGDPD" (IMF) / "NY.GDP.MKTP.CD" (World Bank).
        country: Country name or ISO code.
        years: Number of years of historical data (default 5).

    Returns:
        ToolResponse with list of EconomicIndicator observations.
    """
    errors: list[str] = []

    try:
        series = await fetch_macro_series(indicator, country, years=years)
    except Exception as exc:
        logger.exception("Failed to fetch macro series %s for %s", indicator, country)
        series = []
        errors.append(f"Error: {exc}")

    return ToolResponse(
        data={
            "indicator": indicator,
            "country": country,
            "years_requested": years,
            "observations": [s.model_dump() for s in series],
        },
        confidence=Confidence.HIGH if len(series) >= years else (Confidence.MEDIUM if series else Confidence.LOW),
        sources=[
            SourceReference(
                name="IMF DataMapper",
                url="https://www.imf.org/external/datamapper",
                accessed_at=datetime.utcnow(),
            ),
            SourceReference(
                name="World Bank Open Data",
                url="https://data.worldbank.org",
                accessed_at=datetime.utcnow(),
            ),
        ],
        errors=errors,
    ).model_dump()


@mcp.tool()
async def estimate_sanction_impact(
    target_country: str,
    sanction_type: str,
) -> dict:
    """Estimate the economic impact of sanctions on a target country.

    Uses a heuristic model combining trade exposure, GDP data, and economic
    openness to produce an order-of-magnitude impact estimate. This is a
    simplified model for analytical screening, not a rigorous econometric
    forecast.

    Args:
        target_country: Country name or ISO code (e.g. "Russia", "Iran", "CHN").
        sanction_type: Type of sanctions. One of:
                       "comprehensive" — full trade embargo + SWIFT cutoff
                       "sectoral" — sector-specific restrictions
                       "financial" — SWIFT disconnection, asset freezes
                       "energy" — oil/gas price caps, export bans
                       "technology" — export controls on tech/chips
                       "individual" — targeted individual/entity sanctions
                       "trade" — broad trade restrictions / tariffs
                       "arms" — arms embargo only

    Returns:
        ToolResponse with SanctionImpactEstimate including estimated GDP
        contraction, trade loss, and affected sectors.
    """
    errors: list[str] = []
    codes = _resolve_country(target_country)
    iso3 = codes["iso3"]

    sanction_type_lower = sanction_type.strip().lower()
    multiplier = _SANCTION_MULTIPLIERS.get(sanction_type_lower, 0.30)

    # Fetch real GDP data
    try:
        profile = await build_country_profile(target_country)
    except Exception as exc:
        logger.exception("Failed to fetch profile for sanction impact on %s", target_country)
        errors.append(f"Could not fetch economic data: {exc}")
        profile = CountryEconomicProfile(country=target_country)

    # Get country heuristics or defaults
    country_info = _COUNTRY_HEURISTICS.get(iso3, {
        "trade_share": 0.40,
        "openness": 0.30,
        "key_exports": profile.top_sectors[:4] if profile.top_sectors else ["General"],
        "energy_dependence": 0.30,
    })

    trade_share = country_info["trade_share"]
    openness = country_info["openness"]

    # --- Heuristic impact model ---
    #
    # GDP impact formula:
    #   gdp_impact_pct = trade_share * multiplier * (1 - openness) * 100
    #
    # Rationale: A country whose GDP is heavily trade-exposed (high trade_share)
    # faces more pain. A more open/diversified economy (high openness) can
    # reroute trade more easily, dampening the effect. The multiplier reflects
    # how broad the sanctions are.
    #
    # For energy sanctions, we also factor in energy dependence.
    base_impact = trade_share * multiplier * (1.0 - openness)

    if sanction_type_lower == "energy":
        energy_dep = country_info.get("energy_dependence", 0.30)
        base_impact *= (1.0 + energy_dep)  # amplify if energy-dependent

    gdp_impact_pct = round(base_impact * 100, 2)

    # Trade volume impact
    trade_impact_usd: float | None = None
    if profile.gdp_usd:
        trade_impact_usd = round(profile.gdp_usd * trade_share * multiplier, 2)

    # Affected sectors
    affected_sectors = _estimate_affected_sectors(sanction_type_lower, country_info)

    # Confidence based on data availability
    if profile.gdp_usd and iso3 in _COUNTRY_HEURISTICS:
        confidence_str = "MEDIUM"
        confidence = Confidence.MEDIUM
    elif profile.gdp_usd:
        confidence_str = "LOW"
        confidence = Confidence.LOW
    else:
        confidence_str = "LOW"
        confidence = Confidence.LOW

    estimate = SanctionImpactEstimate(
        target_country=target_country,
        gdp_impact_pct=gdp_impact_pct,
        trade_impact_usd=trade_impact_usd,
        sectors_affected=affected_sectors,
        confidence=confidence_str,
    )

    # Build detailed breakdown for the analyst
    detailed_data = estimate.model_dump()
    detailed_data["model_inputs"] = {
        "trade_share_of_gdp": trade_share,
        "sanction_multiplier": multiplier,
        "economy_openness": openness,
        "sanction_type": sanction_type_lower,
    }
    detailed_data["gdp_usd"] = profile.gdp_usd
    detailed_data["methodology"] = (
        "Heuristic model: GDP_impact = trade_share * sanction_multiplier * (1 - openness). "
        "Energy sanctions are amplified by the target's energy export dependence. "
        "This is an order-of-magnitude screening estimate, not a rigorous econometric forecast."
    )

    return ToolResponse(
        data=detailed_data,
        confidence=confidence,
        sources=[
            SourceReference(
                name="IMF DataMapper",
                url="https://www.imf.org/external/datamapper",
                accessed_at=datetime.utcnow(),
            ),
            SourceReference(
                name="World Bank Open Data",
                url="https://data.worldbank.org",
                accessed_at=datetime.utcnow(),
            ),
            SourceReference(
                name="Heuristic Model (internal)",
                url=None,
                accessed_at=datetime.utcnow(),
            ),
        ],
        errors=errors,
    ).model_dump()


# ---------------------------------------------------------------------------
# Standalone entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    mcp.run()
