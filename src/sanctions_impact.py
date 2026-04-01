"""Sanctions Stock Impact Projector — core logic.

Computes projected stock price impact based on dynamically sourced comparable
sanctions cases. Comparable events are sourced via Claude + yfinance validation,
with a static reference dataset as fallback (see comparable_sourcer.py).
"""

from __future__ import annotations

import asyncio
import logging
import math
from datetime import datetime, timedelta
from typing import Any

from .comparable_sourcer import get_dynamic_comparables
from .tools.market.client import YFinanceClient
from .tools.sanctions.client import SanctionsClient
from .tools.screening.client import search_csl

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Color palette for chart lines
# ---------------------------------------------------------------------------
CHART_COLORS = [
    "#58a6ff", "#f0883e", "#a371f7", "#3fb950", "#f85149",
    "#db61a2", "#79c0ff", "#d2a8ff", "#56d4dd", "#e3b341",
    "#ff7b72", "#7ee787",
]

# ---------------------------------------------------------------------------
# Curated reference dataset — US-accessible tickers only
# ---------------------------------------------------------------------------
SANCTIONS_COMPARABLES: list[dict[str, Any]] = [
    # --- OFAC/CCMC designation or Chinese regulatory crackdown ---
    {
        "name": "ZTE Corp",
        "ticker": "0763.HK",
        "sanction_date": "2018-04-16",
        "description": "US Commerce Dept denial order — total export ban",
        "sector": "telecom",
        "sanction_type": "ofac_ccmc",
        # 90d: -44.5%
    },
    {
        "name": "Alibaba",
        "ticker": "BABA",
        "sanction_date": "2020-11-03",
        "description": "ANT Group IPO halted — regulatory crackdown begins",
        "sector": "tech",
        "sanction_type": "ofac_ccmc",
        # 90d: -20.5%
    },
    {
        "name": "Xiaomi",
        "ticker": "1810.HK",
        "sanction_date": "2021-01-14",
        "description": "CCMC blacklist designation — investment ban",
        "sector": "tech",
        "sanction_type": "ofac_ccmc",
        # 30d: -21.0%
    },
    {
        "name": "Full Truck Alliance",
        "ticker": "YMM",
        "sanction_date": "2021-07-02",
        "description": "China cybersecurity probe — data security crackdown",
        "sector": "tech",
        "sanction_type": "ofac_ccmc",
        # 30d: -44.8%
    },
    {
        "name": "Tencent Music",
        "ticker": "TME",
        "sanction_date": "2021-07-24",
        "description": "China tech crackdown — antitrust & data security",
        "sector": "tech",
        "sanction_type": "ofac_ccmc",
        # 90d: -36.3%
    },
    {
        "name": "Bilibili",
        "ticker": "BILI",
        "sanction_date": "2021-07-24",
        "description": "China tech regulatory storm — content/data controls",
        "sector": "tech",
        "sanction_type": "ofac_ccmc",
        # 90d: -28.5%
    },
    {
        "name": "NIO",
        "ticker": "NIO",
        "sanction_date": "2021-07-24",
        "description": "Chinese ADR delisting fears — SEC/PCAOB scrutiny",
        "sector": "tech",
        "sanction_type": "ofac_ccmc",
        # 90d: -11.3%
    },
    {
        "name": "PDD Holdings",
        "ticker": "PDD",
        "sanction_date": "2021-07-24",
        "description": "China tech crackdown — e-commerce regulatory pressure",
        "sector": "tech",
        "sanction_type": "ofac_ccmc",
        # 90d: -30.0%
    },
    {
        "name": "Baidu",
        "ticker": "BIDU",
        "sanction_date": "2021-01-14",
        "description": "CCMC designation — AI/military-linked concerns",
        "sector": "tech",
        "sanction_type": "ofac_ccmc",
        # 60d: -13.0%, 90d: -22.6%
    },
    {
        "name": "Micron",
        "ticker": "MU",
        "sanction_date": "2023-05-21",
        "description": "China retaliatory ban — cybersecurity review failure",
        "sector": "semiconductors",
        "sanction_type": "retaliation",
        # 30d: -10.2%
    },
    {
        "name": "KWEB ETF",
        "ticker": "KWEB",
        "sanction_date": "2021-07-24",
        "description": "China Internet sector-wide sanctions/regulatory impact",
        "sector": "tech",
        "sanction_type": "ofac_ccmc",
        # 90d: -13.1%
    },
    # --- US export control actions ---
    {
        "name": "Nvidia",
        "ticker": "NVDA",
        "sanction_date": "2022-10-07",
        "description": "BIS advanced chip export rule — A100/H100 banned to China",
        "sector": "semiconductors",
        "sanction_type": "us_export_control",
        "industry": "chip_designer",
    },
    {
        "name": "Applied Materials",
        "ticker": "AMAT",
        "sanction_date": "2022-10-07",
        "description": "BIS October 2022 rule — fab equipment export controls",
        "sector": "semiconductors",
        "sanction_type": "us_export_control",
        "industry": "chip_equipment",
    },
    {
        "name": "ASML",
        "ticker": "ASML",
        "sanction_date": "2023-01-28",
        "description": "Dutch EUV export license revoked — US pressure on Netherlands",
        "sector": "semiconductors",
        "sanction_type": "us_export_control",
        "industry": "chip_equipment",
    },
    {
        "name": "Qualcomm",
        "ticker": "QCOM",
        "sanction_date": "2019-05-15",
        "description": "Huawei supply ban — BIS Entity List export restriction",
        "sector": "semiconductors",
        "sanction_type": "us_export_control",
        "industry": "chip_designer",
    },
    {
        "name": "Lam Research",
        "ticker": "LRCX",
        "sanction_date": "2022-10-07",
        "description": "BIS October 2022 rule — etch/deposition equipment export controls",
        "sector": "semiconductors",
        "sanction_type": "us_export_control",
        "industry": "chip_equipment",
    },
    {
        "name": "KLA Corporation",
        "ticker": "KLAC",
        "sanction_date": "2022-10-07",
        "description": "BIS October 2022 rule — process control equipment export controls",
        "sector": "semiconductors",
        "sanction_type": "us_export_control",
        "industry": "chip_equipment",
    },
    {
        "name": "Marvell Technology",
        "ticker": "MRVL",
        "sanction_date": "2022-10-07",
        "description": "BIS October 2022 rule — networking/storage chip China revenue exposure",
        "sector": "semiconductors",
        "sanction_type": "us_export_control",
        "industry": "chip_designer",
    },
    {
        "name": "Western Digital",
        "ticker": "WDC",
        "sanction_date": "2022-10-07",
        "description": "BIS October 2022 rule — NAND/HDD China supply chain exposure",
        "sector": "semiconductors",
        "sanction_type": "us_export_control",
        "industry": "chip_designer",
    },
    {
        "name": "Intel",
        "ticker": "INTC",
        "sanction_date": "2023-10-17",
        "description": "BIS advanced chip rule tightened — Gaudi AI chip China ban",
        "sector": "semiconductors",
        "sanction_type": "us_export_control",
        "industry": "chip_designer",
    },
    {
        "name": "SMIC",
        "ticker": "0981.HK",
        "sanction_date": "2020-12-18",
        "description": "BIS Entity List — US equipment export ban to China's largest foundry",
        "sector": "semiconductors",
        "sanction_type": "us_export_control",
        "industry": "chip_foundry",
    },
    # --- BIS penalty ---
    {
        "name": "Seagate",
        "ticker": "STX",
        "sanction_date": "2023-04-19",
        "description": "BIS $300M fine for Huawei HDD sales in violation of export rules",
        "sector": "semiconductors",
        "sanction_type": "bis_penalty",
    },
    # --- Sectoral energy sanctions ---
    {
        "name": "Gazprom ADR",
        "ticker": "OGZPY",
        "sanction_date": "2022-02-24",
        "description": "EU/US sectoral energy sanctions — Russia Ukraine invasion",
        "sector": "energy",
        "sanction_type": "sectoral",
    },
    # --- SWIFT exclusion ---
    {
        "name": "Sberbank ADR",
        "ticker": "SBRCY",
        "sanction_date": "2022-02-24",
        "description": "SWIFT exclusion — Russia financial sector sanctions",
        "sector": "finance",
        "sanction_type": "swift_cutoff",
    },
    {
        "name": "VTB Bank",
        "ticker": "VTBR.ME",
        "sanction_date": "2022-02-24",
        "description": "SWIFT exclusion + OFAC SDN — Russia financial sector sanctions",
        "sector": "finance",
        "sanction_type": "swift_cutoff",
    },
    # --- OFAC/CCMC additions ---
    {
        "name": "NetEase",
        "ticker": "NTES",
        "sanction_date": "2021-07-24",
        "description": "China tech crackdown — gaming/content regulatory pressure",
        "sector": "tech",
        "sanction_type": "ofac_ccmc",
    },
]

# Sector groupings for filtering
SECTOR_GROUPS: dict[str, list[str]] = {
    "semiconductors": ["semiconductors", "tech", "telecom"],
    "tech": ["tech", "telecom", "semiconductors", "surveillance"],
    "telecom": ["telecom", "tech"],
    "energy": ["energy"],
    "finance": ["finance"],
    "metals": ["metals", "energy"],
    "surveillance": ["surveillance", "tech"],
    "biotech": ["biotech", "tech"],
}

# Window: 60 trading days before to 120 after sanction date
PRE_DAYS = 60
POST_DAYS = 120

# Sector-appropriate benchmark ETFs for excess-return computation.
# Using sector ETFs (rather than SPY) removes sector-wide movements that are
# unrelated to the sanctions event (e.g. the AI boom that drove NVDA +130% after
# Oct 2022 export controls, which was a sector-wide phenomenon, not sanctions alpha).
_SECTOR_BENCHMARK: dict[str, str] = {
    "semiconductors": "SOXX",
    "tech": "QQQ",
    "energy": "XLE",
    "finance": "XLF",
    "metals": "XME",
    "telecom": "IYZ",
    "surveillance": "QQQ",
    "biotech": "XBI",
}
_DEFAULT_BENCHMARK = "SPY"


# ---------------------------------------------------------------------------
# Core functions
# ---------------------------------------------------------------------------

async def get_target_info(ticker: str) -> dict[str, Any]:
    """Fetch target company profile and current price data."""
    yf = YFinanceClient()
    profile, price = await asyncio.gather(
        yf.get_stock_profile(ticker),
        yf.get_price_data(ticker, period="1y"),
    )
    recent_prices_30d = [hp.close for hp in price.historical[-30:]] if price.historical else []
    return {
        "ticker": ticker.upper(),
        "name": profile.name,
        "sector": profile.sector,
        "industry": profile.industry,
        "country": profile.country,
        "market_cap": profile.market_cap,
        "current_price": price.current_price,
        "change_pct": price.change_pct,
        # Internal use only — popped in run_sanctions_impact before API response
        "_recent_prices_30d": recent_prices_30d,
    }


async def get_sanctions_context(ticker: str, company_name: str) -> dict[str, Any]:
    """Check current sanctions status across OFAC, OpenSanctions, and Trade.gov CSL.

    All external API failures are handled gracefully — the sanctions status
    section is supplementary to the main chart projection.
    """
    result: dict[str, Any] = {
        "is_sanctioned": False,
        "lists": [],
        "programs": [],
        "csl_matches": [],
    }

    # Try OFAC + OpenSanctions
    try:
        sanctions_client = SanctionsClient()
        status = await sanctions_client.check_status(company_name)
        result["is_sanctioned"] = status.is_sanctioned
        result["lists"] = status.lists_found
        result["programs"] = status.programs
    except Exception as e:
        logger.warning("Sanctions check failed (continuing): %s", e)

    # Try Trade.gov CSL
    try:
        csl_results = await search_csl(company_name)
        result["csl_matches"] = [
            {
                "name": m.get("name", ""),
                "source": m.get("source", ""),
                "programs": m.get("programs", []),
                "start_date": m.get("start_date"),
            }
            for m in csl_results[:10]
        ]
    except Exception as e:
        logger.warning("CSL check failed (continuing): %s", e)

    return result


async def _fetch_comparable_curve(
    comp: dict[str, Any], color: str
) -> dict[str, Any] | None:
    """Fetch market-adjusted (excess return) price curve for a single comparable.

    Raw price returns are subtracted by the sector benchmark ETF return over the
    same window. This isolates the sanctions-specific impact from broad sector
    movements. For example: NVDA went +130% after Oct 2022 export controls, but
    SOXX (semiconductor ETF) also rallied due to the AI boom — the excess return
    correctly shows the sanctions headwind, not the sector tailwind.

    If the benchmark fetch fails, raw returns are used as fallback.
    """
    ticker = comp.get("ticker")
    if not ticker:
        return None

    sanction_date_str = comp["sanction_date"]
    sanction_dt = datetime.strptime(sanction_date_str, "%Y-%m-%d")

    start_dt = sanction_dt - timedelta(days=120)
    end_dt = sanction_dt + timedelta(days=240)
    start_str = start_dt.strftime("%Y-%m-%d")
    end_str = end_dt.strftime("%Y-%m-%d")

    sector = comp.get("sector", "")
    benchmark_ticker = _SECTOR_BENCHMARK.get(sector, _DEFAULT_BENCHMARK)

    yf = YFinanceClient()
    try:
        historical, benchmark_hist = await asyncio.gather(
            yf.get_price_history_range(ticker, start_str, end_str),
            yf.get_price_history_range(benchmark_ticker, start_str, end_str),
        )
    except Exception:
        logger.warning("Failed to fetch price data for %s", ticker)
        return None

    if not historical or len(historical) < 20:
        logger.warning("Insufficient data for %s (%d points)", ticker,
                       len(historical) if historical else 0)
        return None

    # Build date→price mappings
    prices_by_date: dict[str, float] = {hp.date: hp.close for hp in historical}
    benchmark_by_date: dict[str, float] = {hp.date: hp.close for hp in (benchmark_hist or [])}

    def _event_price(price_map: dict[str, float]) -> float | None:
        for off in range(0, 10):
            d = (sanction_dt - timedelta(days=off)).strftime("%Y-%m-%d")
            if d in price_map:
                return price_map[d]
        return None

    sanction_price = _event_price(prices_by_date)
    if sanction_price is None or sanction_price == 0:
        logger.warning("No sanction-date price for %s", ticker)
        return None

    benchmark_event_price = _event_price(benchmark_by_date)

    # Build sorted (date_obj, stock_price) list
    dated_prices = []
    for date_str, price in prices_by_date.items():
        try:
            dt = datetime.strptime(date_str, "%Y-%m-%d")
            dated_prices.append((dt, price))
        except ValueError:
            continue
    dated_prices.sort(key=lambda x: x[0])

    # Find sanction-date trading-day index
    sanction_idx = None
    for i, (dt, _) in enumerate(dated_prices):
        if sanction_idx is None and dt.strftime("%Y-%m-%d") >= sanction_date_str:
            sanction_idx = i
    if sanction_idx is None:
        sanction_idx = len(dated_prices) - 1

    trading_days: list[tuple[int, float]] = []
    for i, (dt, price) in enumerate(dated_prices):
        day_offset = i - sanction_idx
        if -PRE_DAYS <= day_offset <= POST_DAYS:
            raw_pct = ((price - sanction_price) / sanction_price) * 100

            # Subtract benchmark return to get excess (sanctions-specific) return
            if benchmark_event_price and benchmark_event_price != 0:
                dt_str = dt.strftime("%Y-%m-%d")
                bench_price = benchmark_by_date.get(dt_str)
                if bench_price:
                    benchmark_pct = ((bench_price - benchmark_event_price) / benchmark_event_price) * 100
                    excess_pct = raw_pct - benchmark_pct
                else:
                    excess_pct = raw_pct
            else:
                excess_pct = raw_pct

            trading_days.append((day_offset, round(excess_pct, 2)))

    if len(trading_days) < 20:
        return None

    return {
        "name": comp["name"],
        "ticker": ticker,
        "sanction_date": sanction_date_str,
        "description": comp["description"],
        "sector": comp.get("sector", ""),
        "sanction_type": comp.get("sanction_type", ""),
        "industry": comp.get("industry", ""),
        "color": color,
        "curve": [{"day": d, "pct": p} for d, p in trading_days],
    }


async def get_comparable_curves(
    comparables: list[dict[str, Any]],
    industry_filter: str | None = None,
) -> list[dict[str, Any]]:
    """Fetch normalized price curves for a pre-selected list of comparable events.

    comparables is supplied by get_dynamic_comparables (or the static fallback).
    industry_filter is an optional in-memory sub-filter applied on top.
    """
    # Sub-filter by chip industry type when relevant (cheap, no API cost)
    if industry_filter:
        industry_filtered = [
            c for c in comparables
            if c.get("industry", "") == industry_filter
        ]
        if len(industry_filtered) >= 3:
            comparables = industry_filtered

    tasks = [
        _fetch_comparable_curve(comp, CHART_COLORS[i % len(CHART_COLORS)])
        for i, comp in enumerate(comparables)
    ]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    curves = []
    for r in results:
        if isinstance(r, dict):
            curves.append(r)
        elif isinstance(r, Exception):
            logger.warning("Comparable curve fetch error: %s", r)

    return curves


def compute_projection(
    comparable_curves: list[dict[str, Any]],
    target_current_price: float,
    target_sector: str | None = None,
    target_sanction_type: str | None = None,
    target_prices_30d: list[float] | None = None,
) -> dict[str, Any]:
    """Compute weighted mean projection + volatility-scaled confidence band.

    Weighting per comparable curve:
      recency   = exp(-0.10 * years_since_event)
      sector_w  = 1.0 if exact sector match else 0.5
      type_w    = 1.0 if exact sanction_type match else 0.5
      raw_w     = recency * sector_w * type_w
      w_i       = raw_w_i / sum(raw_w_j)   (normalized to sum=1)

    Confidence band width is scaled by the target's 30-day realized volatility
    relative to a 30% annualized baseline (band_scale clamped to [0.5, 2.0]).
    High-volatility targets get wider bands; low-volatility targets get narrower ones.
    The mean projection is not directionally adjusted — only band width changes.
    """
    if not comparable_curves or not target_current_price:
        return {"mean": [], "upper": [], "lower": [], "summary": {}}

    # --- Per-curve weights ---
    today = datetime.utcnow().date()
    raw_weights: list[float] = []
    for curve_data in comparable_curves:
        try:
            event_dt = datetime.strptime(curve_data["sanction_date"], "%Y-%m-%d").date()
            years = (today - event_dt).days / 365.25
        except (ValueError, KeyError):
            years = 5.0
        recency = math.exp(-0.10 * years)

        sector_w = 1.0 if (target_sector and curve_data.get("sector") == target_sector) else 0.5
        type_w   = 1.0 if (target_sanction_type and curve_data.get("sanction_type") == target_sanction_type) else 0.5

        raw_weights.append(recency * sector_w * type_w)

    # --- Trimmed mean: drop top/bottom 20% of curves by day-30 excess return ---
    # Eliminates outliers (e.g. NVDA AI-boom recovery, extreme delisting cases) before
    # computing the mean. Only applied when ≥5 curves exist so we don't over-trim small sets.
    if len(comparable_curves) >= 5:
        day30_vals: list[float] = []
        for curve_data in comparable_curves:
            pts = {p["day"]: p["pct"] for p in curve_data["curve"]}
            near = [d for d in pts if 0 <= d <= 40]
            day30_vals.append(pts[min(near, key=lambda d: abs(d - 30))] if near else 0.0)

        n = len(comparable_curves)
        trim = max(1, n // 5)
        keep = set(sorted(range(n), key=lambda i: day30_vals[i])[trim: n - trim])
        comparable_curves = [c for i, c in enumerate(comparable_curves) if i in keep]
        raw_weights      = [w for i, w in enumerate(raw_weights)      if i in keep]

    total_w = sum(raw_weights) or 1.0
    norm_weights = [w / total_w for w in raw_weights]

    # --- Coherence: fraction of post-trim curves with negative day-30 excess return ---
    # direction_agreement = fraction in the majority direction (0.5 = split, 1.0 = unanimous)
    # coherence_low flags when the model lacks directional consensus.
    coherence_score = 1.0
    if comparable_curves:
        day30_signs: list[bool] = []
        for curve_data in comparable_curves:
            pts = {p["day"]: p["pct"] for p in curve_data["curve"]}
            near = [d for d in pts if 0 <= d <= 40]
            if near:
                day30_signs.append(pts[min(near, key=lambda d: abs(d - 30))] < 0)
        if day30_signs:
            neg_frac = sum(day30_signs) / len(day30_signs)
            coherence_score = max(neg_frac, 1.0 - neg_frac)
    coherence_low = coherence_score < 0.65

    # --- Realized volatility → band scale ---
    band_scale = 1.0
    prices_30d = target_prices_30d or []
    if len(prices_30d) >= 5:
        daily_returns = [
            prices_30d[i] / prices_30d[i - 1] - 1
            for i in range(1, len(prices_30d))
            if prices_30d[i - 1] != 0
        ]
        if daily_returns:
            mean_r = sum(daily_returns) / len(daily_returns)
            variance_r = sum((r - mean_r) ** 2 for r in daily_returns) / len(daily_returns)
            realized_vol = math.sqrt(variance_r) * math.sqrt(252)
            band_scale = max(0.5, min(2.0, realized_vol / 0.30))

    # --- Collect (weight, pct) per day ---
    # day → list of (normalized_weight, pct)
    all_day_entries: dict[int, list[tuple[float, float]]] = {}
    for weight, curve_data in zip(norm_weights, comparable_curves):
        for point in curve_data["curve"]:
            all_day_entries.setdefault(point["day"], []).append((weight, point["pct"]))

    mean_curve = []
    upper_band = []
    lower_band = []

    for day in sorted(all_day_entries.keys()):
        entries = all_day_entries[day]
        if len(entries) < 2:
            continue

        # Re-normalize weights for this day (not all curves cover every day)
        day_total = sum(w for w, _ in entries) or 1.0
        day_w = [w / day_total for w, _ in entries]
        day_pcts = [p for _, p in entries]

        mean_pct = sum(w * p for w, p in zip(day_w, day_pcts))
        variance = sum(w * (p - mean_pct) ** 2 for w, p in zip(day_w, day_pcts))
        std_pct = math.sqrt(variance)

        scaled_std = band_scale * std_pct
        projected_price = target_current_price * (1 + mean_pct / 100)
        upper_price = target_current_price * (1 + (mean_pct + scaled_std) / 100)
        lower_price = target_current_price * (1 + (mean_pct - scaled_std) / 100)

        mean_curve.append({
            "day": day,
            "pct": round(mean_pct, 2),
            "price": round(projected_price, 2),
        })
        upper_band.append({
            "day": day,
            "pct": round(mean_pct + scaled_std, 2),
            "price": round(upper_price, 2),
        })
        lower_band.append({
            "day": day,
            "pct": round(mean_pct - scaled_std, 2),
            "price": round(lower_price, 2),
        })

    # --- Summary ---
    pre_pcts = [p["pct"] for p in mean_curve if p["day"] < 0]
    post_pcts_by_day = {p["day"]: p["pct"] for p in mean_curve if p["day"] >= 0}

    summary: dict[str, Any] = {}

    # Pre-event: excess return at the START of the pre-event window (day ~-60) vs day 0.
    # Negative = stock was underperforming its sector benchmark before the event
    #            (risk being priced in — "sell the rumor").
    # Positive = stock was outperforming before the event (market was not anticipating).
    # Using the first point in the window (not max) makes this directly comparable to
    # post-event values, which are also measured from day 0.
    if pre_pcts:
        # pre_pcts is sorted by day (most negative day first) because mean_curve is
        # built from sorted(all_day_entries.keys())
        summary["pre_event_decline"] = round(-pre_pcts[0], 2)

    # Post-announcement trajectory at 30 / 60 / 90 days
    for label, target_day in [("day_30", 30), ("day_60", 60), ("day_90", 90)]:
        # Find nearest available day at or after target
        candidates = [d for d in post_pcts_by_day if d <= target_day]
        if candidates:
            nearest = max(candidates)
            summary[f"{label}_post"] = post_pcts_by_day[nearest]
            # Range from bands
            upper_pts = {p["day"]: p["pct"] for p in upper_band if p["day"] >= 0}
            lower_pts = {p["day"]: p["pct"] for p in lower_band if p["day"] >= 0}
            u_candidates = [d for d in upper_pts if d <= target_day]
            l_candidates = [d for d in lower_pts if d <= target_day]
            if u_candidates and l_candidates:
                summary[f"{label}_range"] = [
                    round(lower_pts[max(l_candidates)], 2),
                    round(upper_pts[max(u_candidates)], 2),
                ]

    # Peak-to-trough across the full window
    all_mean_pcts = [p["pct"] for p in mean_curve]
    if all_mean_pcts:
        summary["max_drawdown"] = round(min(all_mean_pcts), 2)

    return {
        "mean": mean_curve,
        "upper": upper_band,
        "lower": lower_band,
        "summary": summary,
        "coherence_score": round(coherence_score, 3),
        "coherence_low": coherence_low,
    }


async def run_sanctions_impact(ticker: str) -> dict[str, Any]:
    """Top-level entry point: run the full sanctions impact projection."""
    try:
        target_info = await get_target_info(ticker)
    except Exception as e:
        logger.error("Failed to fetch target info for %s: %s", ticker, e)
        raise ValueError(f"Could not find data for ticker '{ticker}'. Check the symbol and try again.") from e
    company_name = target_info.get("name", ticker)

    sanctions_task = get_sanctions_context(ticker, company_name)

    sector = (target_info.get("sector") or "").lower()
    sector_map = {
        "technology": "tech",
        "communication services": "telecom",
        "semiconductors": "semiconductors",
        "energy": "energy",
        "financial services": "finance",
        "financials": "finance",
        "basic materials": "metals",
        "healthcare": "biotech",
    }
    mapped_sector = sector_map.get(sector, sector)

    sanctions_context = await sanctions_task

    country_raw = (target_info.get("country") or "").lower()

    # Infer sanction_type from country + sector (independent of current sanctions status).
    # This determines which reference class best represents the risk scenario for this target.
    inferred_sanction_type: str | None = None

    def _is_western(c: str) -> bool:
        return any(k in c for k in ("united states", "netherlands", "germany", "france",
                                    "united kingdom", "japan", "korea", "taiwan", "australia",
                                    "canada", "israel", "sweden", "finland", "singapore"))

    def _is_chinese(c: str) -> bool:
        return "china" in c or c in ("hong kong", "hk")

    def _is_russian(c: str) -> bool:
        return "russia" in c

    industry_raw = (target_info.get("industry") or "").lower()

    if _is_russian(country_raw) and mapped_sector in ("finance", "financials"):
        inferred_sanction_type = "swift_cutoff"
    elif _is_russian(country_raw) and mapped_sector in ("energy", "metals"):
        inferred_sanction_type = "sectoral"
    elif _is_chinese(country_raw) and (
        mapped_sector in ("tech", "telecom", "semiconductors", "surveillance")
        or any(k in industry_raw for k in ("internet", "software", "e-commerce", "electronic", "semiconductor"))
    ):
        inferred_sanction_type = "ofac_ccmc"
    elif _is_western(country_raw) and mapped_sector in ("semiconductors", "tech", "telecom"):
        inferred_sanction_type = "us_export_control"

    # If the target is already sanctioned under a known program, that overrides the above
    programs = [p.upper() for p in sanctions_context.get("programs", [])]
    csl_sources = [m.get("source", "").lower() for m in sanctions_context.get("csl_matches", [])]
    if any("entity list" in s or "bis" in s for s in csl_sources):
        inferred_sanction_type = "us_export_control"
    elif any(p in programs for p in ["UKRAINE-EO13661", "RUSSIA-EO14024", "IRAN", "CUBA", "DPRK", "SYRIA"]):
        inferred_sanction_type = "sectoral"
    elif any("swift" in p.lower() for p in programs):
        inferred_sanction_type = "swift_cutoff"

    # Infer industry sub-type for semiconductor companies to differentiate
    # chip designers, equipment makers, and foundries. Applied to ALL semiconductor
    # targets (not just us_export_control) so the industry_filter in get_comparable_curves
    # can always narrow to the right reference class when ≥3 matching comparables exist.
    inferred_industry: str | None = None
    if mapped_sector == "semiconductors" or "semiconductor" in industry_raw:
        _FOUNDRY_KEYWORDS = ("foundry", "contract manufactur", "wafer fabricat", "wafer foundry",
                              "logic foundry", "fab ", "fabrication services")
        _EQUIPMENT_KEYWORDS = ("equipment", "materials", "systems", "instruments", "photonics",
                                "laser", "lithograph", "etch", "deposition", "metrology")
        _DESIGNER_KEYWORDS = ("semiconductor", "computing", "microelectronics", "fabless",
                               "integrated circuit", "chip design", "ic design")
        if any(k in industry_raw for k in _FOUNDRY_KEYWORDS):
            inferred_industry = "chip_foundry"
        elif any(k in industry_raw for k in _EQUIPMENT_KEYWORDS):
            inferred_industry = "chip_equipment"
        elif any(k in industry_raw for k in _DESIGNER_KEYWORDS) or mapped_sector == "semiconductors":
            inferred_industry = "chip_designer"

    recent_prices_30d: list[float] = target_info.pop("_recent_prices_30d", [])

    raw_comparables, sourcing_method = await get_dynamic_comparables(
        sector=mapped_sector or None,
        sanction_type=inferred_sanction_type,
        country=target_info.get("country"),
        static_fallback=SANCTIONS_COMPARABLES,
        sector_groups=SECTOR_GROUPS,
    )

    curves = await get_comparable_curves(
        comparables=raw_comparables,
        industry_filter=inferred_industry,
    )

    current_price = target_info.get("current_price") or 0
    projection = compute_projection(
        curves,
        current_price,
        target_sector=mapped_sector or None,
        target_sanction_type=inferred_sanction_type,
        target_prices_30d=recent_prices_30d,
    )

    target_info["sanctions_status"] = sanctions_context

    return {
        "target": target_info,
        "comparables": curves,
        "projection": projection,
        "metadata": {
            "comparable_count": len(curves),
            "time_window_days": [-PRE_DAYS, POST_DAYS],
            "generated_at": datetime.utcnow().isoformat() + "Z",
            "sourcing_method": sourcing_method,
        },
    }
