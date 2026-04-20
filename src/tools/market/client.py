"""API clients for market data sources: yfinance, SEC EDGAR, and FRED."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Any

import yfinance as yf

from ...common.cache import get_cached, set_cached
from ...common.config import config
from ...common.http_client import fetch_json
from .models import (
    AnalystEstimate,
    ExposureReport,
    HistoricalPrice,
    InstitutionalHolder,
    MacroObservation,
    MacroSeries,
    MarketEntityResult,
    PriceData,
    StockProfile,
)

logger = logging.getLogger(__name__)

# SEC EDGAR requires a descriptive User-Agent header.
SEC_USER_AGENT = "EconWarfareOSINT admin@example.com"
SEC_HEADERS = {"User-Agent": SEC_USER_AGENT, "Accept": "application/json"}

# Well-known pension / sovereign fund keywords for "friendly fire" detection.
PENSION_KEYWORDS = [
    "pension",
    "retirement",
    "calpers",
    "calstrs",
    "teachers",
    "state board",
    "public employees",
    "government",
    "sovereign",
    "norway",
    "adia",
    "gic ",
    "cpp ",
    "caisse",
    "superannuation",
]


# ---------------------------------------------------------------------------
# yfinance client (wrapped in asyncio.to_thread for async compat)
# ---------------------------------------------------------------------------

class YFinanceClient:
    """Wraps the yfinance library with caching and async support."""

    CACHE_NS = "yfinance"

    async def get_stock_profile(self, ticker: str) -> StockProfile:
        """Fetch company profile data for a ticker."""
        cached = get_cached(self.CACHE_NS, action="profile", ticker=ticker)
        if cached is not None:
            return StockProfile.model_validate(cached)

        info = await asyncio.to_thread(self._fetch_info, ticker)
        if info.get("_data_unavailable"):
            logger.warning("get_stock_profile: no data for %s (%s)", ticker, info.get("_reason", ""))
        profile = StockProfile(
            ticker=ticker.upper(),
            name=info.get("longName") or info.get("shortName", ticker),
            market_cap=info.get("marketCap"),
            sector=info.get("sector"),
            industry=info.get("industry"),
            country=info.get("country"),
            exchange=info.get("exchange"),
            description=info.get("longBusinessSummary"),
        )
        result = profile.model_dump()
        if info.get("_data_unavailable"):
            result["data_unavailable"] = True
            result["data_note"] = info.get("_reason", "no market data returned")
        set_cached(result, self.CACHE_NS, action="profile", ticker=ticker)
        return profile

    async def get_price_data(self, ticker: str, period: str = "1y") -> PriceData:
        """Fetch current price and historical close prices."""
        cached = get_cached(self.CACHE_NS, action="price", ticker=ticker, period=period)
        if cached is not None:
            return PriceData.model_validate(cached)

        info = await asyncio.to_thread(self._fetch_info, ticker)
        hist_df = await asyncio.to_thread(self._fetch_history, ticker, period)

        historical: list[HistoricalPrice] = []
        if hist_df is not None and not hist_df.empty:
            for idx, row in hist_df.iterrows():
                dt_str = idx.strftime("%Y-%m-%d") if hasattr(idx, "strftime") else str(idx)
                close_val = row.get("Close")
                if close_val is not None:
                    # Handle both scalar and series-like values
                    try:
                        close_float = float(close_val.iloc[0]) if hasattr(close_val, "iloc") else float(close_val)
                    except (TypeError, ValueError, IndexError):
                        continue
                    historical.append(HistoricalPrice(date=dt_str, close=round(close_float, 4)))

        prev_close = info.get("regularMarketPreviousClose") or info.get("previousClose")
        current = info.get("regularMarketPrice") or info.get("currentPrice")
        change_pct = None
        if current and prev_close and prev_close != 0:
            change_pct = round(((current - prev_close) / prev_close) * 100, 4)

        price = PriceData(
            ticker=ticker.upper(),
            current_price=current,
            change_pct=change_pct,
            volume=info.get("regularMarketVolume") or info.get("volume"),
            fifty_two_week_high=info.get("fiftyTwoWeekHigh"),
            fifty_two_week_low=info.get("fiftyTwoWeekLow"),
            historical=historical,
        )
        set_cached(price.model_dump(), self.CACHE_NS, action="price", ticker=ticker, period=period)
        return price

    async def get_institutional_holders(self, ticker: str) -> list[InstitutionalHolder]:
        """Fetch institutional holders via yfinance."""
        cached = get_cached(self.CACHE_NS, action="holders", ticker=ticker)
        if cached is not None:
            return [InstitutionalHolder.model_validate(h) for h in cached]

        holders_df = await asyncio.to_thread(self._fetch_institutional_holders, ticker)
        holders: list[InstitutionalHolder] = []
        if holders_df is not None and not holders_df.empty:
            for _, row in holders_df.iterrows():
                holders.append(InstitutionalHolder(
                    holder_name=str(row.get("Holder", "")),
                    shares=int(row["Shares"]) if row.get("Shares") is not None else None,
                    value=float(row["Value"]) if row.get("Value") is not None else None,
                    pct_held=float(row["% Out"]) if row.get("% Out") is not None else (
                        float(row["pctHeld"]) if row.get("pctHeld") is not None else None
                    ),
                    date_reported=str(row.get("Date Reported", "")) or None,
                ))
        set_cached([h.model_dump() for h in holders], self.CACHE_NS, action="holders", ticker=ticker)
        return holders

    async def get_analyst_estimate(self, ticker: str) -> AnalystEstimate:
        """Fetch analyst consensus data."""
        cached = get_cached(self.CACHE_NS, action="analyst", ticker=ticker)
        if cached is not None:
            return AnalystEstimate.model_validate(cached)

        info = await asyncio.to_thread(self._fetch_info, ticker)
        estimate = AnalystEstimate(
            target_price=info.get("targetMeanPrice"),
            recommendation=info.get("recommendationKey"),
            num_analysts=info.get("numberOfAnalystOpinions"),
        )
        set_cached(estimate.model_dump(), self.CACHE_NS, action="analyst", ticker=ticker)
        return estimate

    # --- synchronous helpers (run via to_thread) ---

    @staticmethod
    def _fetch_info(ticker: str) -> dict[str, Any]:
        t = yf.Ticker(ticker)
        try:
            info = t.info or {}
        except Exception as exc:
            logger.warning("yfinance .info failed for %s: %s", ticker, exc)
            return {"_data_unavailable": True, "_reason": str(exc)}
        has_price = info.get("regularMarketPrice") or info.get("currentPrice")
        has_name = info.get("longName") or info.get("shortName")
        if not has_price and not has_name:
            logger.info("Ticker %s returned no market data — likely not publicly traded or delisted", ticker)
            info["_data_unavailable"] = True
            info["_reason"] = "no price or name data returned by yfinance"
        return info

    @staticmethod
    def _fetch_history(ticker: str, period: str) -> Any:
        t = yf.Ticker(ticker)
        try:
            return t.history(period=period)
        except Exception:
            logger.warning("yfinance .history failed for %s", ticker, exc_info=True)
            return None

    @staticmethod
    def _fetch_history_range(ticker: str, start: str, end: str) -> Any:
        t = yf.Ticker(ticker)
        try:
            return t.history(start=start, end=end)
        except Exception:
            logger.warning("yfinance .history range failed for %s", ticker, exc_info=True)
            return None

    async def get_price_history_range(
        self, ticker: str, start: str, end: str
    ) -> list[HistoricalPrice]:
        """Fetch historical prices between specific dates (YYYY-MM-DD)."""
        cached = get_cached(self.CACHE_NS, action="hist_range", ticker=ticker, start=start, end=end)
        if cached is not None:
            return [HistoricalPrice.model_validate(h) for h in cached]

        hist_df = await asyncio.to_thread(self._fetch_history_range, ticker, start, end)
        historical: list[HistoricalPrice] = []
        if hist_df is not None and not hist_df.empty:
            for idx, row in hist_df.iterrows():
                dt_str = idx.strftime("%Y-%m-%d") if hasattr(idx, "strftime") else str(idx)
                close_val = row.get("Close")
                if close_val is not None:
                    try:
                        close_float = float(close_val.iloc[0]) if hasattr(close_val, "iloc") else float(close_val)
                    except (TypeError, ValueError, IndexError):
                        continue
                    historical.append(HistoricalPrice(date=dt_str, close=round(close_float, 4)))

        set_cached(
            [h.model_dump() for h in historical],
            self.CACHE_NS, action="hist_range", ticker=ticker, start=start, end=end,
        )
        return historical

    @staticmethod
    def _fetch_institutional_holders(ticker: str) -> Any:
        t = yf.Ticker(ticker)
        try:
            return t.institutional_holders
        except Exception:
            logger.warning("yfinance .institutional_holders failed for %s", ticker, exc_info=True)
            return None


# ---------------------------------------------------------------------------
# SEC EDGAR client
# ---------------------------------------------------------------------------

class SECEdgarClient:
    """Client for the SEC EDGAR full-text search and XBRL APIs."""

    CACHE_NS = "sec_edgar"
    SEARCH_URL = "https://efts.sec.gov/LATEST/search-index"
    COMPANY_SEARCH_URL = "https://efts.sec.gov/LATEST/search-index"
    FULL_TEXT_SEARCH_URL = "https://efts.sec.gov/LATEST/search-index"
    COMPANY_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
    COMPANY_FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"
    SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"

    async def search_entities(self, query: str) -> list[MarketEntityResult]:
        """Search SEC EDGAR for entities matching a query string.

        Uses the SEC company tickers JSON endpoint as the primary source,
        falling back to the EFTS full-text search API.
        """
        cached = get_cached(self.CACHE_NS, action="search", query=query)
        if cached is not None:
            return [MarketEntityResult.model_validate(r) for r in cached]

        results: list[MarketEntityResult] = []

        # Try the company_tickers.json file (lightweight, comprehensive)
        try:
            tickers_data = await fetch_json(
                self.COMPANY_TICKERS_URL, headers=SEC_HEADERS
            )
            query_lower = query.lower()
            for _key, entry in tickers_data.items():
                name = entry.get("title", "")
                tick = entry.get("ticker", "")
                if query_lower in name.lower() or query_lower in tick.lower():
                    cik_raw = entry.get("cik_str", "")
                    results.append(MarketEntityResult(
                        name=name,
                        ticker=tick,
                        cik=str(cik_raw).zfill(10),
                        source="sec_edgar",
                        exchange=None,
                    ))
                if len(results) >= 20:
                    break
        except Exception:
            logger.warning("SEC company_tickers lookup failed for '%s'", query, exc_info=True)

        # Fallback: EFTS full-text search
        if not results:
            try:
                data = await fetch_json(
                    "https://efts.sec.gov/LATEST/search-index",
                    params={"q": query, "dateRange": "custom"},
                    headers=SEC_HEADERS,
                )
                for hit in (data.get("hits", {}).get("hits", []) or [])[:15]:
                    src = hit.get("_source", {})
                    results.append(MarketEntityResult(
                        name=src.get("entity_name", src.get("display_names", [query])[0]),
                        ticker=None,
                        cik=src.get("entity_id"),
                        source="sec_edgar",
                    ))
            except Exception:
                logger.warning("SEC EFTS search failed for '%s'", query, exc_info=True)

        set_cached([r.model_dump() for r in results], self.CACHE_NS, action="search", query=query)
        return results

    async def get_company_facts(self, cik: str) -> dict[str, Any]:
        """Retrieve XBRL company facts for a given CIK (zero-padded to 10 digits)."""
        cik_padded = cik.zfill(10)
        cached = get_cached(self.CACHE_NS, action="facts", cik=cik_padded)
        if cached is not None:
            return cached

        url = self.COMPANY_FACTS_URL.format(cik=cik_padded)
        try:
            data = await fetch_json(url, headers=SEC_HEADERS)
        except Exception:
            logger.warning("SEC company facts failed for CIK %s", cik_padded, exc_info=True)
            data = {}

        set_cached(data, self.CACHE_NS, action="facts", cik=cik_padded)
        return data

    async def get_submissions(self, cik: str) -> dict[str, Any]:
        """Retrieve recent filings/submissions for a CIK."""
        cik_padded = cik.zfill(10)
        cached = get_cached(self.CACHE_NS, action="submissions", cik=cik_padded)
        if cached is not None:
            return cached

        url = self.SUBMISSIONS_URL.format(cik=cik_padded)
        try:
            data = await fetch_json(url, headers=SEC_HEADERS)
        except Exception:
            logger.warning("SEC submissions failed for CIK %s", cik_padded, exc_info=True)
            data = {}

        set_cached(data, self.CACHE_NS, action="submissions", cik=cik_padded)
        return data

    async def get_insider_filings(
        self, name: str, limit: int = 5
    ) -> list[dict[str, Any]]:
        """Search EDGAR for recent Form 4 filings that mention the named individual.

        Form 4 = "Statement of Changes in Beneficial Ownership" — filed whenever
        a company officer, director, or 10%-shareholder buys or sells stock.
        Searching by person name reveals which public companies the subject is an
        insider at and the recency of their transaction activity.

        Results are cached 1 h. Returns an empty list on any failure.
        """
        cached = get_cached(self.CACHE_NS, action="form4", name=name)
        if cached is not None:
            return cached

        start_dt = (
            datetime.utcnow() - timedelta(days=730)
        ).strftime("%Y-%m-%d")

        try:
            data = await fetch_json(
                "https://efts.sec.gov/LATEST/search-index",
                params={
                    "q": f'"{name}"',
                    "forms": "4",
                    "dateRange": "custom",
                    "startdt": start_dt,
                },
                headers=SEC_HEADERS,
            )
        except Exception as exc:
            logger.warning("EDGAR Form 4 search failed for %r: %s", name, exc)
            return []

        results: list[dict[str, Any]] = []
        for hit in (data.get("hits", {}).get("hits", []) or [])[:limit]:
            src = hit.get("_source", {})
            display = src.get("display_names") or []
            results.append({
                "form_type": src.get("form_type", "4"),
                "company": src.get("entity_name") or (display[0] if display else "?"),
                "file_date": src.get("file_date"),
                "period_of_report": src.get("period_of_report"),
            })

        set_cached(results, self.CACHE_NS, ttl=3600, action="form4", name=name)
        return results


# ---------------------------------------------------------------------------
# FRED client
# ---------------------------------------------------------------------------

class FREDClient:
    """Client for the Federal Reserve Economic Data (FRED) API."""

    CACHE_NS = "fred"
    BASE_URL = "https://api.stlouisfed.org/fred"

    # Well-known series relevant to economic warfare analysis.
    KNOWN_SERIES = {
        "VIXCLS": "CBOE Volatility Index (VIX)",
        "DGS10": "10-Year Treasury Constant Maturity Rate",
        "DEXUSEU": "U.S. / Euro Foreign Exchange Rate",
        "DTWEXBGS": "Trade Weighted U.S. Dollar Index",
        "CPIAUCSL": "Consumer Price Index for All Urban Consumers",
        "UNRATE": "Unemployment Rate",
        "GDP": "Gross Domestic Product",
    }

    def _api_key(self) -> str:
        key = config.fred_api_key
        if not key:
            logger.warning("FRED_API_KEY not set — FRED requests will fail")
        return key

    async def get_series(self, series_id: str, period: str = "1y") -> MacroSeries:
        """Fetch a FRED time series with observations for the given period."""
        cached = get_cached(self.CACHE_NS, action="series", series_id=series_id, period=period)
        if cached is not None:
            return MacroSeries.model_validate(cached)

        api_key = self._api_key()
        start_date = self._period_to_start_date(period)

        # Fetch series metadata
        title = self.KNOWN_SERIES.get(series_id.upper())
        units: str | None = None
        frequency: str | None = None
        try:
            meta = await fetch_json(
                f"{self.BASE_URL}/series",
                params={
                    "series_id": series_id,
                    "api_key": api_key,
                    "file_type": "json",
                },
            )
            sinfo = (meta.get("seriess") or [{}])[0]
            title = title or sinfo.get("title")
            units = sinfo.get("units")
            frequency = sinfo.get("frequency")
        except Exception:
            logger.warning("FRED series metadata failed for %s", series_id, exc_info=True)

        # Fetch observations
        observations: list[MacroObservation] = []
        try:
            obs_data = await fetch_json(
                f"{self.BASE_URL}/series/observations",
                params={
                    "series_id": series_id,
                    "api_key": api_key,
                    "file_type": "json",
                    "observation_start": start_date,
                },
            )
            for obs in obs_data.get("observations", []):
                val_str = obs.get("value", ".")
                val = None if val_str == "." else float(val_str)
                observations.append(MacroObservation(date=obs["date"], value=val))
        except Exception:
            logger.warning("FRED observations failed for %s", series_id, exc_info=True)

        series = MacroSeries(
            series_id=series_id.upper(),
            title=title,
            units=units,
            frequency=frequency,
            observations=observations,
        )
        set_cached(series.model_dump(), self.CACHE_NS, action="series", series_id=series_id, period=period)
        return series

    async def search_series(self, query: str, limit: int = 10) -> list[dict[str, Any]]:
        """Search FRED for series matching a query string."""
        cached = get_cached(self.CACHE_NS, action="search", query=query, limit=limit)
        if cached is not None:
            return cached

        api_key = self._api_key()
        results: list[dict[str, Any]] = []
        try:
            data = await fetch_json(
                f"{self.BASE_URL}/series/search",
                params={
                    "search_text": query,
                    "api_key": api_key,
                    "file_type": "json",
                    "limit": limit,
                },
            )
            for s in data.get("seriess", []):
                results.append({
                    "series_id": s.get("id"),
                    "title": s.get("title"),
                    "units": s.get("units"),
                    "frequency": s.get("frequency"),
                    "popularity": s.get("popularity"),
                })
        except Exception:
            logger.warning("FRED series search failed for '%s'", query, exc_info=True)

        set_cached(results, self.CACHE_NS, action="search", query=query, limit=limit)
        return results

    @staticmethod
    def _period_to_start_date(period: str) -> str:
        """Convert a human period string (e.g. '1y', '6m', '5y') to YYYY-MM-DD."""
        now = datetime.utcnow()
        period = period.strip().lower()
        if period.endswith("y"):
            years = int(period[:-1])
            start = now - timedelta(days=365 * years)
        elif period.endswith("m"):
            months = int(period[:-1])
            start = now - timedelta(days=30 * months)
        elif period.endswith("d"):
            days = int(period[:-1])
            start = now - timedelta(days=days)
        else:
            start = now - timedelta(days=365)
        return start.strftime("%Y-%m-%d")


# ---------------------------------------------------------------------------
# Exposure analysis helper
# ---------------------------------------------------------------------------

def _is_pension_or_sovereign(name: str) -> bool:
    """Heuristic check if an institutional holder name looks like a pension/sovereign fund."""
    lower = name.lower()
    return any(kw in lower for kw in PENSION_KEYWORDS)


async def build_exposure_report(ticker: str) -> ExposureReport:
    """Build a "friendly fire" exposure report for a ticker.

    Combines yfinance institutional holder data with analyst estimates to
    assess how much US/allied capital is exposed to the target entity.
    """
    yf_client = YFinanceClient()

    profile = await yf_client.get_stock_profile(ticker)
    holders = await yf_client.get_institutional_holders(ticker)
    analyst = await yf_client.get_analyst_estimate(ticker)

    pension_holders = [h for h in holders if _is_pension_or_sovereign(h.holder_name)]
    total_usd = sum(h.value for h in holders if h.value is not None)

    return ExposureReport(
        entity_name=profile.name,
        ticker=ticker.upper(),
        us_institutional_holders=holders,
        total_us_exposure_usd=total_usd if total_usd > 0 else None,
        pension_fund_exposure=pension_holders,
        analyst_estimate=analyst,
    )
