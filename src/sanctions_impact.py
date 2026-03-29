"""Sanctions Stock Impact Projector — core logic.

Computes projected stock price impact based on historical comparable
sanctions cases. Designed for fast, deterministic demo use (no LLM calls).
"""

from __future__ import annotations

import asyncio
import logging
import math
from datetime import datetime, timedelta
from typing import Any

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
    return {
        "ticker": ticker.upper(),
        "name": profile.name,
        "sector": profile.sector,
        "industry": profile.industry,
        "country": profile.country,
        "market_cap": profile.market_cap,
        "current_price": price.current_price,
        "change_pct": price.change_pct,
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
    """Fetch and normalize price curve for a single comparable."""
    ticker = comp.get("ticker")
    if not ticker:
        return None

    sanction_date_str = comp["sanction_date"]
    sanction_dt = datetime.strptime(sanction_date_str, "%Y-%m-%d")

    # Fetch data around the actual sanction date, not relative to today
    start_dt = sanction_dt - timedelta(days=120)
    end_dt = sanction_dt + timedelta(days=240)
    start_str = start_dt.strftime("%Y-%m-%d")
    end_str = end_dt.strftime("%Y-%m-%d")

    yf = YFinanceClient()
    try:
        historical = await yf.get_price_history_range(ticker, start_str, end_str)
    except Exception:
        logger.warning("Failed to fetch price data for %s", ticker)
        return None

    if not historical or len(historical) < 20:
        logger.warning("Insufficient data for %s (%d points)", ticker,
                       len(historical) if historical else 0)
        return None

    # Build date→price mapping
    prices_by_date: dict[str, float] = {}
    for hp in historical:
        prices_by_date[hp.date] = hp.close

    # Find sanction-date price (or nearest prior trading day)
    sanction_price: float | None = None
    for offset in range(0, 10):
        check_date = (sanction_dt - timedelta(days=offset)).strftime("%Y-%m-%d")
        if check_date in prices_by_date:
            sanction_price = prices_by_date[check_date]
            break

    if sanction_price is None or sanction_price == 0:
        logger.warning("No sanction-date price for %s", ticker)
        return None

    # Build sorted list of (date_obj, price)
    dated_prices = []
    for date_str, price in prices_by_date.items():
        try:
            dt = datetime.strptime(date_str, "%Y-%m-%d")
            dated_prices.append((dt, price))
        except ValueError:
            continue
    dated_prices.sort(key=lambda x: x[0])

    # Find the trading-day index of the sanction date (or nearest)
    trading_days: list[tuple[int, float]] = []
    sanction_idx = None
    for i, (dt, _price) in enumerate(dated_prices):
        if sanction_idx is None and dt.strftime("%Y-%m-%d") >= sanction_date_str:
            sanction_idx = i

    if sanction_idx is None:
        sanction_idx = len(dated_prices) - 1

    for i, (dt, price) in enumerate(dated_prices):
        day_offset = i - sanction_idx
        if -PRE_DAYS <= day_offset <= POST_DAYS:
            pct_change = ((price - sanction_price) / sanction_price) * 100
            trading_days.append((day_offset, round(pct_change, 2)))

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
    sector_filter: str | None = None,
    sanction_type: str | None = None,
    industry_filter: str | None = None,
) -> list[dict[str, Any]]:
    """Fetch normalized price curves for all comparable sanctions cases."""
    comparables = SANCTIONS_COMPARABLES

    # Filter by sanction_type first (most specific)
    if sanction_type:
        type_filtered = [
            c for c in comparables
            if c.get("sanction_type", "") == sanction_type
        ]
        if len(type_filtered) >= 3:
            comparables = type_filtered

    # Sub-filter by industry within the sanction_type subset
    if industry_filter:
        industry_filtered = [
            c for c in comparables
            if c.get("industry", "") == industry_filter
        ]
        if len(industry_filtered) >= 3:
            comparables = industry_filtered

    # Then filter by sector within the remaining subset
    if sector_filter:
        related_sectors = SECTOR_GROUPS.get(sector_filter.lower(), [sector_filter.lower()])
        sector_filtered = [
            c for c in comparables
            if c.get("sector", "").lower() in related_sectors
        ]
        if len(sector_filtered) >= 3:
            comparables = sector_filtered

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
) -> dict[str, Any]:
    """Compute mean projection + confidence band from comparable curves.

    Summary includes both pre-event decline (anticipatory selloff) and
    post-announcement trajectory, so anticipated vs. surprise events read correctly.
    """
    if not comparable_curves or not target_current_price:
        return {"mean": [], "upper": [], "lower": [], "summary": {}}

    # Collect values for all days (pre and post)
    all_day_values: dict[int, list[float]] = {}
    for curve_data in comparable_curves:
        for point in curve_data["curve"]:
            all_day_values.setdefault(point["day"], []).append(point["pct"])

    mean_curve = []
    upper_band = []
    lower_band = []

    for day in sorted(all_day_values.keys()):
        values = all_day_values[day]
        if len(values) < 2:
            continue

        mean_pct = sum(values) / len(values)
        variance = sum((v - mean_pct) ** 2 for v in values) / (len(values) - 1)
        std_pct = math.sqrt(variance)

        projected_price = target_current_price * (1 + mean_pct / 100)
        upper_price = target_current_price * (1 + (mean_pct + std_pct) / 100)
        lower_price = target_current_price * (1 + (mean_pct - std_pct) / 100)

        mean_curve.append({
            "day": day,
            "pct": round(mean_pct, 2),
            "price": round(projected_price, 2),
        })
        upper_band.append({
            "day": day,
            "pct": round(mean_pct + std_pct, 2),
            "price": round(upper_price, 2),
        })
        lower_band.append({
            "day": day,
            "pct": round(mean_pct - std_pct, 2),
            "price": round(lower_price, 2),
        })

    # --- Summary ---
    pre_pcts = [p["pct"] for p in mean_curve if p["day"] < 0]
    post_pcts_by_day = {p["day"]: p["pct"] for p in mean_curve if p["day"] >= 0}

    summary: dict[str, Any] = {}

    # Pre-event: drop from pre-event peak down to announcement day (day 0 = 0%).
    # Positive pre_pcts = stock was higher before event (anticipated selloff into day 0).
    # pre_event_decline is negative: -(peak above day-0) = how much was priced in already.
    if pre_pcts:
        pre_event_peak = max(pre_pcts)  # how high stock was relative to day-0 price
        summary["pre_event_decline"] = round(-pre_event_peak, 2)  # negative = already priced in

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

    # Infer industry sub-type for us_export_control to differentiate chip designers vs equipment makers
    inferred_industry: str | None = None
    if inferred_sanction_type == "us_export_control":
        _EQUIPMENT_KEYWORDS = ("equipment", "materials", "systems", "instruments", "photonics", "laser")
        _DESIGNER_KEYWORDS = ("semiconductor", "computing", "microelectronics", "fabless", "integrated circuit")
        if any(k in industry_raw for k in _EQUIPMENT_KEYWORDS):
            inferred_industry = "chip_equipment"
        elif any(k in industry_raw for k in _DESIGNER_KEYWORDS) or mapped_sector == "semiconductors":
            inferred_industry = "chip_designer"

    curves = await get_comparable_curves(
        sector_filter=mapped_sector or None,
        sanction_type=inferred_sanction_type,
        industry_filter=inferred_industry,
    )

    current_price = target_info.get("current_price") or 0
    projection = compute_projection(curves, current_price)

    target_info["sanctions_status"] = sanctions_context

    return {
        "target": target_info,
        "comparables": curves,
        "projection": projection,
        "metadata": {
            "comparable_count": len(curves),
            "time_window_days": [-PRE_DAYS, POST_DAYS],
            "generated_at": datetime.utcnow().isoformat() + "Z",
        },
    }
