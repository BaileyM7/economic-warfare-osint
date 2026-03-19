"""Async API clients for FRED, IMF, and World Bank economic data."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any

from src.common.cache import get_cached, set_cached
from src.common.config import config
from src.common.http_client import fetch_json

from .models import CommodityPrice, CountryEconomicProfile, EconomicIndicator

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Country name / ISO-2 / ISO-3 mapping (subset relevant to economic warfare)
# ---------------------------------------------------------------------------
_COUNTRY_ALIASES: dict[str, dict[str, str]] = {
    "china": {"iso2": "CN", "iso3": "CHN", "imf": "CHN"},
    "russia": {"iso2": "RU", "iso3": "RUS", "imf": "RUS"},
    "iran": {"iso2": "IR", "iso3": "IRN", "imf": "IRN"},
    "north korea": {"iso2": "KP", "iso3": "PRK", "imf": "PRK"},
    "united states": {"iso2": "US", "iso3": "USA", "imf": "USA"},
    "usa": {"iso2": "US", "iso3": "USA", "imf": "USA"},
    "germany": {"iso2": "DE", "iso3": "DEU", "imf": "DEU"},
    "japan": {"iso2": "JP", "iso3": "JPN", "imf": "JPN"},
    "india": {"iso2": "IN", "iso3": "IND", "imf": "IND"},
    "brazil": {"iso2": "BR", "iso3": "BRA", "imf": "BRA"},
    "south korea": {"iso2": "KR", "iso3": "KOR", "imf": "KOR"},
    "saudi arabia": {"iso2": "SA", "iso3": "SAU", "imf": "SAU"},
    "turkey": {"iso2": "TR", "iso3": "TUR", "imf": "TUR"},
    "venezuela": {"iso2": "VE", "iso3": "VEN", "imf": "VEN"},
    "cuba": {"iso2": "CU", "iso3": "CUB", "imf": "CUB"},
    "myanmar": {"iso2": "MM", "iso3": "MMR", "imf": "MMR"},
    "syria": {"iso2": "SY", "iso3": "SYR", "imf": "SYR"},
    "ukraine": {"iso2": "UA", "iso3": "UKR", "imf": "UKR"},
    "united kingdom": {"iso2": "GB", "iso3": "GBR", "imf": "GBR"},
    "france": {"iso2": "FR", "iso3": "FRA", "imf": "FRA"},
    "taiwan": {"iso2": "TW", "iso3": "TWN", "imf": "TWN"},
    "mexico": {"iso2": "MX", "iso3": "MEX", "imf": "MEX"},
    "canada": {"iso2": "CA", "iso3": "CAN", "imf": "CAN"},
    "australia": {"iso2": "AU", "iso3": "AUS", "imf": "AUS"},
    "indonesia": {"iso2": "ID", "iso3": "IDN", "imf": "IDN"},
    "nigeria": {"iso2": "NG", "iso3": "NGA", "imf": "NGA"},
    "south africa": {"iso2": "ZA", "iso3": "ZAF", "imf": "ZAF"},
}

# Typical top sectors by country (fallback when API data is unavailable)
_DEFAULT_SECTORS: dict[str, list[str]] = {
    "CHN": ["Manufacturing", "Technology", "Real Estate", "Finance", "Agriculture"],
    "RUS": ["Energy", "Mining", "Defense", "Agriculture", "Finance"],
    "IRN": ["Oil & Gas", "Petrochemicals", "Mining", "Agriculture", "Automotive"],
    "USA": ["Technology", "Finance", "Healthcare", "Energy", "Manufacturing"],
    "DEU": ["Automotive", "Manufacturing", "Chemicals", "Finance", "Technology"],
    "JPN": ["Automotive", "Electronics", "Finance", "Manufacturing", "Services"],
    "IND": ["IT Services", "Agriculture", "Textiles", "Pharmaceuticals", "Finance"],
    "SAU": ["Oil & Gas", "Petrochemicals", "Finance", "Construction", "Tourism"],
    "VEN": ["Oil & Gas", "Mining", "Agriculture", "Manufacturing", "Finance"],
    "PRK": ["Mining", "Textiles", "Agriculture", "Military Industry", "Fisheries"],
}

# FRED series IDs relevant to economic warfare analysis
FRED_WARFARE_SERIES: dict[str, str] = {
    "oil_wti": "DCOILWTICO",  # WTI crude oil price
    "oil_brent": "DCOILBRENTEU",  # Brent crude oil price
    "vix": "VIXCLS",  # CBOE VIX volatility index
    "treasury_10y": "DGS10",  # 10-Year Treasury yield
    "dollar_index": "DTWEXBGS",  # Trade-weighted US dollar index
    "cpi": "CPIAUCSL",  # Consumer Price Index
    "fed_funds": "FEDFUNDS",  # Federal funds effective rate
    "gold": "GOLDAMGBD228NLBM",  # Gold fixing price (London)
    "natural_gas": "DHHNGSP",  # Henry Hub natural gas spot price
    "unemployment": "UNRATE",  # US unemployment rate
}

# World Bank indicator codes
WB_INDICATORS: dict[str, str] = {
    "gdp": "NY.GDP.MKTP.CD",
    "gdp_growth": "NY.GDP.MKTP.KD.ZG",
    "inflation": "FP.CPI.TOTL.ZG",
    "fdi": "BN.KLT.DINV.CD",
    "unemployment": "SL.UEM.TOTL.ZS",
    "reserves": "FI.RES.TOTL.CD",
    "debt_to_gdp": "GC.DOD.TOTL.GD.ZS",
    "exports": "NE.EXP.GNFS.CD",
    "imports": "NE.IMP.GNFS.CD",
    "trade_pct_gdp": "NE.TRD.GNFS.ZS",
    "current_account": "BN.CAB.XOKA.CD",
}

# IMF indicator codes (DataMapper API)
IMF_INDICATORS: dict[str, str] = {
    "gdp": "NGDPD",  # GDP, current prices (billions USD)
    "gdp_growth": "NGDP_RPCH",  # GDP, constant prices, % change
    "inflation": "PCPIPCH",  # Inflation, average consumer prices, % change
    "current_account": "BCA",  # Current account balance (billions USD)
    "unemployment": "LUR",  # Unemployment rate
    "government_debt": "GGXWDG_NGDP",  # General government gross debt, % GDP
}

# Commodity names to FRED series mapping
COMMODITY_SERIES: dict[str, str] = {
    "oil": "DCOILWTICO",
    "crude": "DCOILWTICO",
    "wti": "DCOILWTICO",
    "brent": "DCOILBRENTEU",
    "gold": "GOLDAMGBD228NLBM",
    "natural gas": "DHHNGSP",
    "gas": "DHHNGSP",
    "copper": "PCOPPUSDM",
    "wheat": "PWHEAMTUSDM",
    "corn": "PMAIZMTUSDM",
    "aluminum": "PALUMUSDM",
    "nickel": "PNICKUSDM",
    "iron ore": "PIORECRUSDM",
}

COMMODITY_UNITS: dict[str, str] = {
    "DCOILWTICO": "USD/barrel",
    "DCOILBRENTEU": "USD/barrel",
    "GOLDAMGBD228NLBM": "USD/troy oz",
    "DHHNGSP": "USD/MMBtu",
    "PCOPPUSDM": "USD/metric ton",
    "PWHEAMTUSDM": "USD/metric ton",
    "PMAIZMTUSDM": "USD/metric ton",
    "PALUMUSDM": "USD/metric ton",
    "PNICKUSDM": "USD/metric ton",
    "PIORECRUSDM": "USD/dry metric ton",
}


def _resolve_country(country: str) -> dict[str, str]:
    """Resolve a country name or code to iso2/iso3/imf codes."""
    key = country.strip().lower()
    if key in _COUNTRY_ALIASES:
        return _COUNTRY_ALIASES[key]
    # Try matching by ISO code directly
    upper = country.strip().upper()
    if len(upper) == 2:
        return {"iso2": upper, "iso3": upper, "imf": upper}
    if len(upper) == 3:
        return {"iso2": upper[:2], "iso3": upper, "imf": upper}
    # Fallback: use as-is for iso3/imf
    return {"iso2": upper[:2], "iso3": upper[:3], "imf": upper[:3]}


# ============================================================================
# FRED Client
# ============================================================================

class FREDClient:
    """Client for the Federal Reserve Economic Data (FRED) API."""

    BASE_URL = "https://api.stlouisfed.org/fred"
    CACHE_NS = "fred"
    CACHE_TTL = 3600  # 1 hour

    def __init__(self, api_key: str | None = None) -> None:
        self.api_key = api_key or config.fred_api_key

    async def get_series_observations(
        self,
        series_id: str,
        start_date: str | None = None,
        end_date: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Fetch observation data for a FRED series.

        Returns a list of {"date": "YYYY-MM-DD", "value": "123.45"} dicts.
        """
        cached = get_cached(
            self.CACHE_NS,
            series_id=series_id,
            start=start_date,
            end=end_date,
            limit=limit,
        )
        if cached is not None:
            return cached

        params: dict[str, Any] = {
            "series_id": series_id,
            "api_key": self.api_key,
            "file_type": "json",
            "sort_order": "desc",
            "limit": limit,
        }
        if start_date:
            params["observation_start"] = start_date
        if end_date:
            params["observation_end"] = end_date

        url = f"{self.BASE_URL}/series/observations"
        data = await fetch_json(url, params=params)
        observations = data.get("observations", [])

        set_cached(
            observations,
            self.CACHE_NS,
            ttl=self.CACHE_TTL,
            series_id=series_id,
            start=start_date,
            end=end_date,
            limit=limit,
        )
        return observations

    async def search_series(self, search_text: str, limit: int = 10) -> list[dict[str, Any]]:
        """Search for FRED series by keyword."""
        cached = get_cached(self.CACHE_NS, action="search", q=search_text, limit=limit)
        if cached is not None:
            return cached

        params = {
            "search_text": search_text,
            "api_key": self.api_key,
            "file_type": "json",
            "limit": limit,
        }
        url = f"{self.BASE_URL}/series/search"
        data = await fetch_json(url, params=params)
        series_list = data.get("seriess", [])

        set_cached(
            series_list,
            self.CACHE_NS,
            ttl=self.CACHE_TTL,
            action="search",
            q=search_text,
            limit=limit,
        )
        return series_list

    async def get_series_info(self, series_id: str) -> dict[str, Any]:
        """Get metadata for a FRED series."""
        cached = get_cached(self.CACHE_NS, action="info", series_id=series_id)
        if cached is not None:
            return cached

        params = {
            "series_id": series_id,
            "api_key": self.api_key,
            "file_type": "json",
        }
        url = f"{self.BASE_URL}/series"
        data = await fetch_json(url, params=params)
        series_info = data.get("seriess", [{}])[0]

        set_cached(
            series_info,
            self.CACHE_NS,
            ttl=self.CACHE_TTL,
            action="info",
            series_id=series_id,
        )
        return series_info

    async def get_commodity_price(
        self,
        commodity: str,
        period_days: int = 365,
    ) -> list[CommodityPrice]:
        """Get commodity price time series from FRED."""
        series_id = COMMODITY_SERIES.get(commodity.lower())
        if not series_id:
            return []

        start_date = (datetime.utcnow() - timedelta(days=period_days)).strftime("%Y-%m-%d")
        observations = await self.get_series_observations(
            series_id, start_date=start_date, limit=500
        )

        unit = COMMODITY_UNITS.get(series_id, "USD")
        results: list[CommodityPrice] = []
        prev_value: float | None = None

        for obs in reversed(observations):  # chronological order
            raw = obs.get("value", ".")
            if raw == ".":
                continue
            try:
                val = float(raw)
            except (ValueError, TypeError):
                continue
            change = None
            if prev_value and prev_value != 0:
                change = round(((val - prev_value) / prev_value) * 100, 4)
            results.append(
                CommodityPrice(
                    commodity=commodity,
                    price=val,
                    unit=unit,
                    currency="USD",
                    date=obs.get("date", ""),
                    change_pct=change,
                )
            )
            prev_value = val

        return results


# ============================================================================
# IMF Client
# ============================================================================

class IMFClient:
    """Client for the IMF DataMapper API."""

    BASE_URL = "https://www.imf.org/external/datamapper/api/v1"
    CACHE_NS = "imf"
    CACHE_TTL = 7200  # 2 hours (data updates infrequently)

    async def get_indicator(
        self,
        indicator: str,
        country_code: str,
    ) -> dict[str, Any]:
        """Fetch an IMF indicator for a country.

        Returns dict like {"2020": 14.72, "2021": 17.73, ...} (year -> value).
        """
        cached = get_cached(
            self.CACHE_NS, indicator=indicator, country=country_code
        )
        if cached is not None:
            return cached

        url = f"{self.BASE_URL}/{indicator}/{country_code}"
        data = await fetch_json(url)

        # Response shape: {"values": {"<indicator>": {"<country>": {"<year>": value}}}}
        values = (
            data.get("values", {})
            .get(indicator, {})
            .get(country_code, {})
        )

        set_cached(
            values,
            self.CACHE_NS,
            ttl=self.CACHE_TTL,
            indicator=indicator,
            country=country_code,
        )
        return values

    async def get_latest_value(
        self,
        indicator: str,
        country_code: str,
    ) -> tuple[float | None, str]:
        """Get the most recent value for an indicator/country pair.

        Returns (value, year_string).
        """
        yearly_data = await self.get_indicator(indicator, country_code)
        if not yearly_data:
            return None, ""

        # Find the most recent year with a non-null value
        for year in sorted(yearly_data.keys(), reverse=True):
            val = yearly_data[year]
            if val is not None:
                try:
                    return float(val), year
                except (ValueError, TypeError):
                    continue
        return None, ""

    async def get_time_series(
        self,
        indicator: str,
        country_code: str,
        years: int = 5,
    ) -> list[EconomicIndicator]:
        """Get a multi-year time series as a list of EconomicIndicator."""
        yearly_data = await self.get_indicator(indicator, country_code)
        if not yearly_data:
            return []

        indicator_name = IMF_INDICATORS.get(indicator, indicator)
        results: list[EconomicIndicator] = []
        sorted_years = sorted(yearly_data.keys(), reverse=True)

        for year in sorted_years[:years]:
            val = yearly_data[year]
            if val is None:
                continue
            try:
                fval = float(val)
            except (ValueError, TypeError):
                continue
            results.append(
                EconomicIndicator(
                    indicator_id=indicator,
                    name=indicator_name,
                    country=country_code,
                    value=fval,
                    unit="",
                    date=year,
                    source="IMF",
                )
            )

        return results


# ============================================================================
# World Bank Client
# ============================================================================

class WorldBankClient:
    """Client for the World Bank Open Data API."""

    BASE_URL = "https://api.worldbank.org/v2"
    CACHE_NS = "worldbank"
    CACHE_TTL = 7200  # 2 hours

    async def get_indicator(
        self,
        country_iso2: str,
        indicator_code: str,
        per_page: int = 50,
    ) -> list[dict[str, Any]]:
        """Fetch World Bank indicator data for a country.

        Returns list of yearly observations.
        """
        cached = get_cached(
            self.CACHE_NS,
            country=country_iso2,
            indicator=indicator_code,
            per_page=per_page,
        )
        if cached is not None:
            return cached

        url = f"{self.BASE_URL}/country/{country_iso2}/indicator/{indicator_code}"
        params = {"format": "json", "per_page": per_page}
        data = await fetch_json(url, params=params)

        # World Bank response: [metadata, [observations]]
        if not isinstance(data, list) or len(data) < 2:
            return []
        observations = data[1] if data[1] else []

        set_cached(
            observations,
            self.CACHE_NS,
            ttl=self.CACHE_TTL,
            country=country_iso2,
            indicator=indicator_code,
            per_page=per_page,
        )
        return observations

    async def get_latest_value(
        self,
        country_iso2: str,
        indicator_code: str,
    ) -> tuple[float | None, str]:
        """Get the most recent non-null value for an indicator.

        Returns (value, year_string).
        """
        observations = await self.get_indicator(country_iso2, indicator_code)
        for obs in observations:
            val = obs.get("value")
            if val is not None:
                try:
                    return float(val), str(obs.get("date", ""))
                except (ValueError, TypeError):
                    continue
        return None, ""

    async def get_time_series(
        self,
        country_iso2: str,
        indicator_code: str,
        years: int = 5,
    ) -> list[EconomicIndicator]:
        """Get a multi-year time series as a list of EconomicIndicator."""
        observations = await self.get_indicator(country_iso2, indicator_code)
        results: list[EconomicIndicator] = []
        count = 0

        for obs in observations:
            if count >= years:
                break
            val = obs.get("value")
            if val is None:
                continue
            try:
                fval = float(val)
            except (ValueError, TypeError):
                continue

            ind_name = obs.get("indicator", {}).get("value", indicator_code)
            results.append(
                EconomicIndicator(
                    indicator_id=indicator_code,
                    name=ind_name,
                    country=country_iso2,
                    value=fval,
                    unit="",
                    date=str(obs.get("date", "")),
                    source="World Bank",
                )
            )
            count += 1

        return results


# ============================================================================
# Unified helpers used by the server
# ============================================================================

async def build_country_profile(country: str) -> CountryEconomicProfile:
    """Assemble a country economic profile from IMF + World Bank data.

    Tries IMF first for each indicator, falls back to World Bank.
    """
    codes = _resolve_country(country)
    iso2 = codes["iso2"]
    iso3 = codes["iso3"]
    imf_code = codes["imf"]

    imf = IMFClient()
    wb = WorldBankClient()

    # GDP (nominal, billions USD from IMF -> convert to USD)
    gdp_val, _ = await imf.get_latest_value("NGDPD", imf_code)
    gdp_usd: float | None = None
    if gdp_val is not None:
        gdp_usd = gdp_val * 1_000_000_000  # IMF reports in billions
    else:
        gdp_usd, _ = await wb.get_latest_value(iso2, WB_INDICATORS["gdp"])

    # GDP growth
    gdp_growth, _ = await imf.get_latest_value("NGDP_RPCH", imf_code)
    if gdp_growth is None:
        gdp_growth, _ = await wb.get_latest_value(iso2, WB_INDICATORS["gdp_growth"])

    # Inflation
    inflation, _ = await imf.get_latest_value("PCPIPCH", imf_code)
    if inflation is None:
        inflation, _ = await wb.get_latest_value(iso2, WB_INDICATORS["inflation"])

    # Unemployment
    unemployment, _ = await imf.get_latest_value("LUR", imf_code)
    if unemployment is None:
        unemployment, _ = await wb.get_latest_value(iso2, WB_INDICATORS["unemployment"])

    # Reserves (World Bank only)
    reserves, _ = await wb.get_latest_value(iso2, WB_INDICATORS["reserves"])

    # Debt-to-GDP
    debt_gdp, _ = await imf.get_latest_value("GGXWDG_NGDP", imf_code)
    if debt_gdp is None:
        debt_gdp, _ = await wb.get_latest_value(iso2, WB_INDICATORS["debt_to_gdp"])

    # FDI inflows
    fdi, _ = await wb.get_latest_value(iso2, WB_INDICATORS["fdi"])

    # Top sectors
    top_sectors = _DEFAULT_SECTORS.get(iso3, ["Services", "Manufacturing", "Agriculture"])

    return CountryEconomicProfile(
        country=country,
        gdp_usd=gdp_usd,
        gdp_growth_pct=round(gdp_growth, 2) if gdp_growth is not None else None,
        inflation_pct=round(inflation, 2) if inflation is not None else None,
        unemployment_pct=round(unemployment, 2) if unemployment is not None else None,
        reserves_usd=reserves,
        debt_to_gdp_pct=round(debt_gdp, 2) if debt_gdp is not None else None,
        fdi_inflows_usd=fdi,
        top_sectors=top_sectors,
    )


async def fetch_macro_series(
    indicator: str,
    country: str,
    years: int = 5,
) -> list[EconomicIndicator]:
    """Fetch a macro time series, trying IMF first then World Bank.

    `indicator` can be a human-readable key (e.g. "gdp", "inflation") or a raw
    indicator code (e.g. "NGDPD", "NY.GDP.MKTP.CD").
    """
    codes = _resolve_country(country)
    iso2 = codes["iso2"]
    imf_code = codes["imf"]

    imf = IMFClient()
    wb = WorldBankClient()

    # Resolve human-readable key to codes
    imf_indicator = IMF_INDICATORS.get(indicator.lower(), indicator)
    wb_indicator = WB_INDICATORS.get(indicator.lower(), indicator)

    # Try IMF first
    results = await imf.get_time_series(imf_indicator, imf_code, years=years)
    if results:
        return results

    # Fallback to World Bank
    results = await wb.get_time_series(iso2, wb_indicator, years=years)
    return results
