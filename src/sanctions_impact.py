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

from .comparable_sourcer import get_dynamic_comparables, get_target_control_peers
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
    {
        "name": "ZTE Corp",
        "ticker": "0763.HK",
        "sanction_date": "2018-04-16",
        "description": "US Commerce Dept denial order — total export ban",
        "sector": "telecom",
        "sanction_type": "ofac_ccmc",
        "severity": "blocking",
        "market_cap_tier": "mid",
    },
    {
        "name": "Alibaba",
        "ticker": "BABA",
        "sanction_date": "2020-11-03",
        "description": "ANT Group IPO halted — regulatory crackdown begins",
        "sector": "tech",
        "sanction_type": "ofac_ccmc",
        "severity": "regulatory_crackdown",
        "market_cap_tier": "mega",
    },
    {
        "name": "Full Truck Alliance",
        "ticker": "YMM",
        "sanction_date": "2021-07-02",
        "description": "China cybersecurity probe — data security crackdown",
        "sector": "tech",
        "sanction_type": "ofac_ccmc",
        "severity": "regulatory_crackdown",
        "market_cap_tier": "mid",
    },
    {
        "name": "Qualcomm",
        "ticker": "QCOM",
        "sanction_date": "2019-05-15",
        "description": "Huawei supply ban — BIS Entity List export restriction",
        "sector": "semiconductors",
        "sanction_type": "us_export_control",
        "severity": "entity_list",
        "market_cap_tier": "large",
        "industry": "chip_designer",
    },
    {
        "name": "Nvidia",
        "ticker": "NVDA",
        "sanction_date": "2022-10-07",
        "description": "BIS advanced chip export rule — A100/H100 banned to China",
        "sector": "semiconductors",
        "sanction_type": "us_export_control",
        "severity": "sectoral",
        "market_cap_tier": "mega",
        "industry": "chip_designer",
    },
    {
        "name": "ASML",
        "ticker": "ASML",
        "sanction_date": "2023-01-28",
        "description": "Dutch EUV export license revoked — US pressure on Netherlands",
        "sector": "semiconductors",
        "sanction_type": "us_export_control",
        "severity": "sectoral",
        "market_cap_tier": "mega",
        "industry": "chip_equipment",
    },
    {
        "name": "SMIC",
        "ticker": "0981.HK",
        "sanction_date": "2020-12-18",
        "description": "BIS Entity List — US equipment ban to largest Chinese foundry",
        "sector": "semiconductors",
        "sanction_type": "us_export_control",
        "severity": "entity_list",
        "market_cap_tier": "mid",
        "industry": "chip_foundry",
    },
    {
        "name": "Seagate",
        "ticker": "STX",
        "sanction_date": "2023-04-19",
        "description": "BIS $300M fine for Huawei HDD sales violating export rules",
        "sector": "semiconductors",
        "sanction_type": "bis_penalty",
        "severity": "entity_list",
        "market_cap_tier": "mid",
    },
    {
        "name": "Gazprom ADR",
        "ticker": "OGZPY",
        "sanction_date": "2022-02-24",
        "description": "EU/US sectoral energy sanctions — Russia Ukraine invasion",
        "sector": "energy",
        "sanction_type": "sectoral",
        "severity": "sectoral",
        "market_cap_tier": "large",
    },
    {
        "name": "Sberbank ADR",
        "ticker": "SBRCY",
        "sanction_date": "2022-02-24",
        "description": "SWIFT exclusion — Russia financial sector sanctions",
        "sector": "finance",
        "sanction_type": "swift_cutoff",
        "severity": "blocking",
        "market_cap_tier": "large",
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

    # Forward-fill benchmark prices so every trading day uses excess returns
    # (avoids mixing raw and excess returns when benchmark has date gaps, e.g. holidays)
    last_known_bench: float | None = benchmark_event_price if benchmark_event_price else None

    trading_days: list[tuple[int, float]] = []
    for i, (dt, price) in enumerate(dated_prices):
        day_offset = i - sanction_idx
        if -PRE_DAYS <= day_offset <= POST_DAYS:
            raw_pct = ((price - sanction_price) / sanction_price) * 100

            # Subtract benchmark return to get excess (sanctions-specific) return.
            # Forward-fill last known benchmark price for days with no benchmark data
            # (e.g. ETF holiday gaps) so all points use the same return basis.
            if benchmark_event_price and benchmark_event_price != 0:
                dt_str = dt.strftime("%Y-%m-%d")
                bench_price = benchmark_by_date.get(dt_str)
                if bench_price:
                    last_known_bench = bench_price
                # Use last known benchmark price (forward-fill) if today is missing
                if last_known_bench and last_known_bench != 0:
                    benchmark_pct = ((last_known_bench - benchmark_event_price) / benchmark_event_price) * 100
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

    # Deduplicate by ticker — if Claude returns the same company twice (different event
    # dates), keep the first occurrence. Prevents duplicate chart lines for the same firm.
    seen_tickers: set[str] = set()
    deduped: list[dict[str, Any]] = []
    for c in comparables:
        t = (c.get("ticker") or "").upper()
        if t and t not in seen_tickers:
            seen_tickers.add(t)
            deduped.append(c)
    comparables = deduped

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


async def get_control_curves(
    raw_comparables: list[dict[str, Any]],
    target_peers: list[str],
) -> list[dict[str, Any]]:
    """Fetch excess-return curves for non-sanctioned peers of the TARGET company.

    target_peers is a list of tickers similar to the queried company (not to the
    individual comparables). Each peer is measured over every comparable's event
    window so the curves are time-aligned to the same shocks. The sector benchmark
    subtraction cancels out broad market moves, leaving only idiosyncratic returns.

    If target_peers is empty, falls back to each comparable's own control_peers list
    (the coarser, comparable-relative peers from SANCTIONS_COMPARABLES).
    """
    # Tickers that are already in the comparable (sanctioned) group — must not appear
    # in the control group, since they represent the sanctioned event itself.
    comparable_tickers: set[str] = {
        (c.get("ticker") or "").upper() for c in raw_comparables if c.get("ticker")
    }

    tasks: list[Any] = []
    color_idx = 0

    # Deduplicate (peer_ticker, sanction_date) pairs — many comparables can share
    # the same event date (e.g. 7 entries on 2021-07-24), so running the same peer
    # across duplicate dates produces identical curves that waste API quota.
    seen_peer_windows: set[tuple[str, str]] = set()

    resolved_peers = target_peers

    if not resolved_peers:
        # Dynamic fallback: target-company peer sourcing failed, so source peers for
        # each comparable individually (treating each comparable as a mini-target).
        # Results are cached per ticker so this is cheap on repeat runs.
        peer_tasks = [
            get_target_control_peers(
                ticker=comp["ticker"],
                company_name=comp.get("name", comp["ticker"]),
                sector=comp.get("sector"),
                industry=comp.get("industry"),
            )
            for comp in raw_comparables
            if comp.get("ticker")
        ]
        fallback_results = await asyncio.gather(*peer_tasks, return_exceptions=True)
        fallback_set: set[str] = set()
        for r in fallback_results:
            if isinstance(r, list):
                fallback_set.update(r)
        resolved_peers = list(fallback_set)

    if not resolved_peers:
        return []

    for comp in raw_comparables:
        peers: list[str] = resolved_peers
        sanction_date = comp["sanction_date"]
        for peer_ticker in peers:
            peer_upper = peer_ticker.upper()
            # Skip if this peer is already a sanctioned comparable
            if peer_upper in comparable_tickers:
                continue
            # Skip duplicate (peer, date) windows
            window_key = (peer_upper, sanction_date)
            if window_key in seen_peer_windows:
                continue
            seen_peer_windows.add(window_key)

            peer_comp = {
                "ticker": peer_ticker,
                "sanction_date": sanction_date,
                "sector": comp.get("sector", ""),
                "name": peer_ticker,
                "description": "Non-sanctioned peer",
                "sanction_type": comp.get("sanction_type", ""),
                "industry": "",
            }
            tasks.append(
                _fetch_comparable_curve(peer_comp, CHART_COLORS[color_idx % len(CHART_COLORS)])
            )
            color_idx += 1

    if not tasks:
        return []

    results = await asyncio.gather(*tasks, return_exceptions=True)
    raw_curves: list[dict[str, Any]] = []
    for r in results:
        if isinstance(r, dict):
            raw_curves.append(r)
        elif isinstance(r, Exception):
            logger.warning("Control peer curve fetch error: %s", r)

    # Aggregate: each peer appears once with its excess return averaged across
    # all comparable event windows. This prevents the same ticker showing up
    # N times in the legend (once per comparable).
    curves_by_ticker: dict[str, list[dict[str, Any]]] = {}
    for c in raw_curves:
        curves_by_ticker.setdefault(c["ticker"], []).append(c)

    aggregated: list[dict[str, Any]] = []
    for ticker_str, ticker_curves in curves_by_ticker.items():
        # Average pct at each day across all event windows
        day_pcts: dict[int, list[float]] = {}
        for c in ticker_curves:
            for pt in c["curve"]:
                day_pcts.setdefault(pt["day"], []).append(pt["pct"])

        avg_curve = [
            {"day": day, "pct": round(sum(pcts) / len(pcts), 2)}
            for day, pcts in sorted(day_pcts.items())
        ]

        ref = ticker_curves[0]
        aggregated.append({
            "name": ticker_str,
            "ticker": ticker_str,
            "sanction_date": "",          # not meaningful for an averaged curve
            "description": f"Non-sanctioned peer (avg {len(ticker_curves)} windows)",
            "sector": ref.get("sector", ""),
            "sanction_type": "",
            "industry": "",
            "color": ref["color"],
            "curve": avg_curve,
        })

    logger.debug(
        "Control group: %d raw curves → %d aggregated peers",
        len(raw_curves), len(aggregated),
    )
    return aggregated


_SEVERITY_ADJACENCY: dict[str, set[str]] = {
    "blocking": {"entity_list"},
    "entity_list": {"blocking", "sectoral"},
    "sectoral": {"entity_list", "delisting_threat"},
    "delisting_threat": {"sectoral", "regulatory_crackdown"},
    "regulatory_crackdown": {"delisting_threat"},
}

_CAP_TIERS = ("mega", "large", "mid", "small")


def _severity_weight(target_sev: str | None, comp_sev: str | None) -> float:
    if not target_sev or not comp_sev:
        return 0.7
    if target_sev == comp_sev:
        return 1.0
    if comp_sev in _SEVERITY_ADJACENCY.get(target_sev, set()):
        return 0.6
    return 0.3


def _cap_tier_weight(target_tier: str | None, comp_tier: str | None) -> float:
    if not target_tier or not comp_tier:
        return 0.7
    try:
        t_idx = _CAP_TIERS.index(target_tier)
        c_idx = _CAP_TIERS.index(comp_tier)
    except ValueError:
        return 0.7
    diff = abs(t_idx - c_idx)
    if diff == 0:
        return 1.0
    if diff == 1:
        return 0.7
    return 0.4


def infer_cap_tier(market_cap: float | None) -> str:
    if not market_cap or market_cap <= 0:
        return "mid"
    if market_cap >= 200e9:
        return "mega"
    if market_cap >= 20e9:
        return "large"
    if market_cap >= 2e9:
        return "mid"
    return "small"


def compute_projection(
    comparable_curves: list[dict[str, Any]],
    target_current_price: float,
    target_sector: str | None = None,
    target_sanction_type: str | None = None,
    target_prices_30d: list[float] | None = None,
    *,
    target_severity: str | None = None,
    target_cap_tier: str | None = None,
) -> dict[str, Any]:
    """Compute weighted mean projection + volatility-scaled confidence band.

    Weighting per comparable curve (Phase D):
      recency    = exp(-0.10 * years_since_event)
      sector_w   = 1.0 if exact sector match else 0.5
      type_w     = 1.0 if exact sanction_type match else 0.5
      sev_w      = severity match (1.0 / 0.6 / 0.3)
      cap_w      = cap-tier match (1.0 / 0.7 / 0.4)
      cluster_w  = 1/N for N events on the same date

    Two-phase projection:
      Shock phase (days 0-7): severity exponent 1.5 (higher severity influence)
      Structural phase (days 13+): severity exponent 1.0
      Days 8-12: linear blend between the two phases
    """
    if not comparable_curves or not target_current_price:
        return {"mean": [], "upper": [], "lower": [], "summary": {}}

    today = datetime.utcnow().date()

    from collections import Counter
    date_cluster_counts: Counter = Counter(
        c.get("sanction_date", "") for c in comparable_curves
    )

    # Per-curve weight components: (base_w, sev_w)
    # base_w includes everything except severity so the per-day loop can apply
    # a phase-dependent severity exponent.
    curve_weight_parts: list[tuple[float, float]] = []
    for curve_data in comparable_curves:
        try:
            event_dt = datetime.strptime(curve_data["sanction_date"], "%Y-%m-%d").date()
            years = (today - event_dt).days / 365.25
        except (ValueError, KeyError):
            years = 5.0
        recency = math.exp(-0.10 * years)

        sector_w = 1.0 if (target_sector and curve_data.get("sector") == target_sector) else 0.5
        type_w   = 1.0 if (target_sanction_type and curve_data.get("sanction_type") == target_sanction_type) else 0.5

        cluster_n = date_cluster_counts.get(curve_data.get("sanction_date", ""), 1)
        cluster_w = 1.0 / max(cluster_n, 1)

        sev_w = _severity_weight(target_severity, curve_data.get("severity"))
        cap_w = _cap_tier_weight(target_cap_tier, curve_data.get("market_cap_tier"))

        base_w = recency * sector_w * type_w * cluster_w * cap_w
        curve_weight_parts.append((base_w, sev_w))

    # --- Trimmed mean: drop top/bottom 20% by day-30 excess return ---
    if len(comparable_curves) >= 5:
        day30_vals: list[float] = []
        for curve_data in comparable_curves:
            pts = {p["day"]: p["pct"] for p in curve_data["curve"]}
            near = [d for d in pts if 0 <= d <= 40]
            day30_vals.append(pts[min(near, key=lambda d: abs(d - 30))] if near else 0.0)

        n = len(comparable_curves)
        trim = max(1, n // 5)
        keep = set(sorted(range(n), key=lambda i: day30_vals[i])[trim: n - trim])
        comparable_curves   = [c for i, c in enumerate(comparable_curves)   if i in keep]
        curve_weight_parts  = [w for i, w in enumerate(curve_weight_parts)  if i in keep]

    # --- Coherence score ---
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

    # --- Per-day data: day -> list of (curve_index, pct) ---
    all_day_entries: dict[int, list[tuple[int, float]]] = {}
    for i, curve_data in enumerate(comparable_curves):
        for point in curve_data["curve"]:
            all_day_entries.setdefault(point["day"], []).append((i, point["pct"]))

    mean_curve: list[dict[str, Any]] = []
    upper_band: list[dict[str, Any]] = []
    lower_band: list[dict[str, Any]] = []

    for day in sorted(all_day_entries.keys()):
        entries = all_day_entries[day]
        if len(entries) < 2:
            continue

        # Two-phase severity exponent: shock (1.5) → structural (1.0)
        if day <= 7:
            sev_exp = 1.5
        elif day <= 12:
            sev_exp = 1.5 - 0.1 * (day - 7)
        else:
            sev_exp = 1.0

        raw_w_list: list[float] = []
        pct_list: list[float] = []
        for curve_idx, pct in entries:
            base_w, sev_w = curve_weight_parts[curve_idx]
            raw_w_list.append(base_w * (sev_w ** sev_exp))
            pct_list.append(pct)

        day_total = sum(raw_w_list) or 1.0
        day_w = [w / day_total for w in raw_w_list]

        mean_pct = sum(w * p for w, p in zip(day_w, pct_list))
        variance = sum(w * (p - mean_pct) ** 2 for w, p in zip(day_w, pct_list))
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

    if pre_pcts:
        summary["pre_event_decline"] = round(-pre_pcts[0], 2)

    for label, target_day in [("day_30", 30), ("day_60", 60), ("day_90", 90)]:
        candidates = [d for d in post_pcts_by_day if d <= target_day]
        if candidates:
            nearest = max(candidates)
            summary[f"{label}_post"] = post_pcts_by_day[nearest]
            upper_pts = {p["day"]: p["pct"] for p in upper_band if p["day"] >= 0}
            lower_pts = {p["day"]: p["pct"] for p in lower_band if p["day"] >= 0}
            u_candidates = [d for d in upper_pts if d <= target_day]
            l_candidates = [d for d in lower_pts if d <= target_day]
            if u_candidates and l_candidates:
                summary[f"{label}_range"] = [
                    round(lower_pts[max(l_candidates)], 2),
                    round(upper_pts[max(u_candidates)], 2),
                ]

    all_mean_pcts = [p["pct"] for p in mean_curve]
    if all_mean_pcts:
        summary["max_drawdown"] = round(min(all_mean_pcts), 2)

    # Shock trough: minimum mean excess return in days 0-10
    shock_pts = [p["pct"] for p in mean_curve if 0 <= p["day"] <= 10]
    if shock_pts:
        summary["shock_trough"] = round(min(shock_pts), 2)

    # Recovery day: first day after the overall trough where mean recovers 50% of drawdown
    post_mean_pts = [p for p in mean_curve if p["day"] >= 0]
    if post_mean_pts:
        trough_pct = min(p["pct"] for p in post_mean_pts)
        trough_day = next(p["day"] for p in post_mean_pts if p["pct"] == trough_pct)
        recovery_threshold = trough_pct * 0.5 if trough_pct < 0 else 0.0
        recovery_found = None
        for p in post_mean_pts:
            if p["day"] > trough_day and p["pct"] >= recovery_threshold:
                recovery_found = p["day"]
                break
        summary["recovery_day"] = recovery_found
        summary["terminal_pct"] = post_mean_pts[-1]["pct"]

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

    # --- Infer severity and cap tier for weighting ---
    inferred_severity: str | None = None
    if inferred_sanction_type in ("swift_cutoff",):
        inferred_severity = "blocking"
    elif inferred_sanction_type == "ofac_ccmc":
        if sanctions_context.get("is_sanctioned"):
            inferred_severity = "blocking"
        else:
            inferred_severity = "regulatory_crackdown"
    elif inferred_sanction_type == "us_export_control":
        if any("entity list" in s for s in [m.get("source", "").lower() for m in sanctions_context.get("csl_matches", [])]):
            inferred_severity = "entity_list"
        else:
            inferred_severity = "sectoral"
    elif inferred_sanction_type == "sectoral":
        inferred_severity = "sectoral"
    elif inferred_sanction_type in ("bis_penalty", "retaliation"):
        inferred_severity = "entity_list"

    target_market_cap = target_info.get("market_cap")
    target_cap_tier = infer_cap_tier(target_market_cap)

    raw_comparables, sourcing_method = await get_dynamic_comparables(
        sector=mapped_sector or None,
        sanction_type=inferred_sanction_type,
        country=target_info.get("country"),
        static_fallback=SANCTIONS_COMPARABLES,
        sector_groups=SECTOR_GROUPS,
        severity=inferred_severity,
        market_cap=target_market_cap,
        sub_sector=industry_raw or None,
    )

    # Collect sanctioned comparable tickers for cross-list dedup
    used_tickers: set[str] = {
        (c.get("ticker") or "").upper()
        for c in raw_comparables
        if c.get("ticker")
    }

    # Build a short sanctions context string for the peers prompt
    _ctx_parts = [inferred_sanction_type or "general"]
    if inferred_severity:
        _ctx_parts.append(f"severity: {inferred_severity}")
    if sanctions_context.get("programs"):
        _ctx_parts.append(f"programs: {', '.join(sanctions_context['programs'][:3])}")
    sanctions_context_str = "; ".join(_ctx_parts)

    # Fetch target-similar peers and comparable curves in parallel
    target_peers, curves = await asyncio.gather(
        get_target_control_peers(
            ticker=ticker,
            company_name=company_name,
            sector=mapped_sector or None,
            industry=industry_raw or None,
            market_cap=target_market_cap,
            excluded_tickers=used_tickers,
            sanctions_context_str=sanctions_context_str,
        ),
        get_comparable_curves(
            comparables=raw_comparables,
            industry_filter=inferred_industry,
        ),
    )

    # Belt-and-suspenders: filter out any control peers that overlap with comparables
    target_peers = [t for t in target_peers if t.upper() not in used_tickers]

    control_curves = await get_control_curves(raw_comparables, target_peers)

    current_price = target_info.get("current_price") or 0
    projection = compute_projection(
        curves,
        current_price,
        target_sector=mapped_sector or None,
        target_sanction_type=inferred_sanction_type,
        target_prices_30d=recent_prices_30d,
        target_severity=inferred_severity,
        target_cap_tier=target_cap_tier,
    )
    control_projection = compute_projection(
        control_curves,
        current_price,
        target_sector=mapped_sector or None,
        target_sanction_type=inferred_sanction_type,
        target_prices_30d=recent_prices_30d,
        target_severity=inferred_severity,
        target_cap_tier=target_cap_tier,
    )

    target_info["sanctions_status"] = sanctions_context

    return {
        "target": target_info,
        "comparables": curves,
        "projection": projection,
        "control_comparables": control_curves,
        "control_projection": control_projection,
        "metadata": {
            "comparable_count": len(curves),
            "control_peer_count": len(control_curves),
            "control_peer_tickers": target_peers,
            "time_window_days": [-PRE_DAYS, POST_DAYS],
            "generated_at": datetime.utcnow().isoformat() + "Z",
            "sourcing_method": sourcing_method,
            "inferred_severity": inferred_severity,
            "inferred_cap_tier": target_cap_tier,
        },
    }
