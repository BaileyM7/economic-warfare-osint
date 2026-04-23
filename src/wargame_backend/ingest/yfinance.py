"""Yahoo Finance market-data adapter.

Data source
-----------
We hit Yahoo's chart endpoint directly
(``https://query1.finance.yahoo.com/v8/finance/chart/{ticker}``) instead of
adding the ``yfinance`` package as a dependency — it's a thin HTTP wrapper
around the same endpoint and the package's scrape-and-DataFrame layer
isn't worth the extra runtime weight here.

Per ticker we fetch the daily bars in [since, until], compute the
day-over-day percentage change for each bar, and emit one Event per
(ticker, day).  The yfinance signal extractor surfaces the largest
absolute |%change| of the day across all watched tickers for a country.

Auth
----
None.  Yahoo throttles aggressive callers — keep ticker count modest and
tolerate 429s via the existing retry middleware.

Dedup key
---------
``yfinance:{ticker}:{date}``
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import datetime, timezone
from typing import Any, ClassVar

import structlog
from pydantic import BaseModel, Field

from wargame_backend.app.db.models import Event, EventDomain
from ingest.base import Source, RawRecord

log = structlog.get_logger(__name__)

# Yahoo aggressively throttles bare httpx requests.  A browser-like
# User-Agent + modest per-ticker spacing keeps us under their unofficial
# per-IP threshold for public chart endpoints.
_YAHOO_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/125.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
}
_PER_TICKER_SPACING_SECONDS = 1.5

# Tickers of interest for the 10 slice countries.
SLICE_TICKERS: dict[str, list[tuple[str, str]]] = {
    "CHN": [("000001.SS", "Shanghai Composite"), ("KWEB", "KraneShares CSI Internet")],
    "TWN": [("^TWII", "TAIEX"), ("TSM", "TSMC ADR")],
    "USA": [("^GSPC", "S&P 500"), ("LMT", "Lockheed Martin"), ("RTX", "RTX")],
    "JPN": [("^N225", "Nikkei 225")],
    "KOR": [("^KS11", "KOSPI")],
    "IND": [("^BSESN", "SENSEX")],
    "AUS": [("^AXJO", "ASX 200")],
    "RUS": [("IMOEX.ME", "MOEX")],
    "PRK": [],  # no public market
    "PHL": [("PSEI.PS", "PSE Composite")],
}

# Currency pairs — bilateral USD pairs only; a fall in USDTWD = TWD strength.
CURRENCY_PAIRS: list[tuple[str, str, str]] = [
    ("USDCNY=X", "CHN", "USD/CNY"),
    ("USDTWD=X", "TWN", "USD/TWD"),
    ("USDJPY=X", "JPN", "USD/JPY"),
    ("USDKRW=X", "KOR", "USD/KRW"),
    ("USDINR=X", "IND", "USD/INR"),
    ("USDPHP=X", "PHL", "USD/PHP"),
    ("USDAUD=X", "AUS", "USD/AUD"),
]

_CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"


class YFinanceRawRecord(BaseModel):
    """One ticker daily bar with computed pct_change vs prior close."""

    ticker: str
    label: str
    iso3: str
    date: str  # YYYY-MM-DD
    close: float
    prior_close: float | None = None
    pct_change: float | None = None
    raw: dict[str, Any] = Field(default_factory=dict)


def _safe_pct_change(curr: float, prior: float | None) -> float | None:
    if prior is None or prior == 0:
        return None
    return round(((curr - prior) / prior) * 100.0, 3)


def _all_targets() -> list[tuple[str, str, str]]:
    """Return [(ticker, iso3, label)] across both equities and FX pairs."""
    out: list[tuple[str, str, str]] = []
    for iso3, pairs in SLICE_TICKERS.items():
        for ticker, label in pairs:
            out.append((ticker, iso3, label))
    for ticker, iso3, label in CURRENCY_PAIRS:
        out.append((ticker, iso3, label))
    return out


class YFinanceSource(Source):
    """Yahoo Finance market-data adapter — emits one Event per (ticker, day)."""

    name: ClassVar[str] = "yfinance"
    display_name: ClassVar[str] = "Yahoo Finance"

    async def fetch(
        self, since: datetime, until: datetime
    ) -> AsyncIterator[RawRecord]:
        period1 = int(since.timestamp())
        period2 = int(until.timestamp())
        for idx, (ticker, iso3, label) in enumerate(_all_targets()):
            # Space requests to stay under Yahoo's per-IP throttle.  The
            # chart endpoint is the cheapest API they expose, but they still
            # 429 under a burst — 1.5 s between tickers keeps us safe.
            if idx > 0:
                await asyncio.sleep(_PER_TICKER_SPACING_SECONDS)
            url = _CHART_URL.format(ticker=ticker)
            params = {
                "period1": period1,
                "period2": period2,
                "interval": "1d",
                "events": "history",
            }
            try:
                response = await self._get(url, params=params, headers=_YAHOO_HEADERS)
                payload = response.json()
            except Exception as exc:  # noqa: BLE001
                log.warning("yfinance.fetch_failed", ticker=ticker, error=str(exc))
                continue

            chart = (payload.get("chart") or {}).get("result") or []
            if not chart:
                continue
            result = chart[0]
            timestamps = result.get("timestamp") or []
            indicators = (result.get("indicators") or {}).get("quote") or [{}]
            closes = indicators[0].get("close") or []

            prior_close: float | None = None
            for ts, close in zip(timestamps, closes):
                if close is None:
                    continue
                date = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")
                pct = _safe_pct_change(float(close), prior_close)
                yield YFinanceRawRecord(
                    ticker=ticker,
                    label=label,
                    iso3=iso3,
                    date=date,
                    close=float(close),
                    prior_close=prior_close,
                    pct_change=pct,
                    raw={"timestamp": ts, "close": close},
                )
                prior_close = float(close)

    async def normalize(self, raw: RawRecord) -> Event:
        assert isinstance(raw, YFinanceRawRecord)
        try:
            occurred_at = datetime.strptime(raw.date, "%Y-%m-%d").replace(
                tzinfo=timezone.utc
            )
        except ValueError:
            occurred_at = datetime.now(timezone.utc)

        dedup_key = f"yfinance:{raw.ticker}:{raw.date}"
        return Event(
            source="yfinance",
            occurred_at=occurred_at,
            actor_iso3=raw.iso3,
            target_iso3=None,
            event_type=f"market_close_{raw.ticker.replace('^', '').replace('=', '_').lower()}",
            domain=EventDomain.economic,
            severity=None,
            payload={
                "_dedup_key": dedup_key,
                "ticker": raw.ticker,
                "label": raw.label,
                "iso3": raw.iso3,
                "date": raw.date,
                "close": raw.close,
                "prior_close": raw.prior_close,
                "pct_change": raw.pct_change,
            },
            raw_text=(
                f"yfinance {raw.ticker} {raw.date}: close {raw.close:.2f}"
                + (f" ({raw.pct_change:+.2f}%)" if raw.pct_change is not None else "")
            ),
        )
