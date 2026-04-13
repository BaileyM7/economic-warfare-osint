"""Monitoring Dashboard router — KPIs, activity feed, map data, macro indicators."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from fastapi import APIRouter

from src.db import get_db, row_to_activity
from src.tools.market.client import YFinanceClient

logger = logging.getLogger(__name__)


def _yf_ticker_history(symbol: str, period: str = "10d"):
    """Sync helper to fetch yfinance ticker history (called via asyncio.to_thread)."""
    import yfinance as yf
    return yf.Ticker(symbol).history(period=period)

router = APIRouter(prefix="/api", tags=["monitoring"])


# --- Geo lookup for COA target entities ---

_ENTITY_GEO: dict[str, tuple[float, float]] = {
    "smic": (31.2, 121.5), "huawei": (22.7, 114.1), "hisilicon": (22.7, 114.1),
    "ymtc": (30.7, 111.3), "zte": (22.5, 114.1), "alibaba": (30.3, 120.2),
    "baba": (30.3, 120.2), "tsmc": (24.8, 121.0), "samsung": (37.4, 127.0),
    "central state bank": (39.9, 32.9), "eastern shipping": (1.3, 103.8),
    "oceanic holdings": (1.3, 103.8), "pacific maritime": (1.3, 103.8),
    "ganfeng lithium": (27.1, 114.9), "tianqi lithium": (30.6, 104.1),
    "catl": (26.7, 119.3), "china mobile": (39.9, 116.4), "xiaomi": (39.9, 116.4),
    "baidu": (39.9, 116.4), "tencent": (22.5, 114.1), "nio": (31.2, 121.5),
    "asml": (51.4, 5.5), "intel": (37.4, -122.0), "qualcomm": (32.9, -117.2),
    "micron": (43.6, -116.2), "applied materials": (37.4, -122.1),
    "lady m": (1.3, 103.8), "russia": (55.8, 37.6), "iran": (35.7, 51.4),
    "north korea": (39.0, 125.8), "china": (39.9, 116.4), "prc": (39.9, 116.4),
    "singapore": (1.3, 103.8), "taiwan": (25.0, 121.5),
}

_MONITORING_ZONES = [
    {"lat": 14.5, "lon": 114.0, "label": "South China Sea", "type": "monitoring_zone", "status": "active"},
    {"lat": 25.0, "lon": 121.5, "label": "Taiwan Strait", "type": "monitoring_zone", "status": "active"},
    {"lat": 2.0, "lon": 103.0, "label": "Malacca Strait", "type": "monitoring_zone", "status": "active"},
    {"lat": 35.0, "lon": 129.0, "label": "Korean Peninsula", "type": "monitoring_zone", "status": "active"},
    {"lat": -6.0, "lon": 106.0, "label": "Sunda Strait", "type": "monitoring_zone", "status": "active"},
]

# --- Macro data cache ---

_macro_cache: dict[str, Any] = {"data": None, "fetched_at": 0.0}
_MACRO_CACHE_TTL = 900  # 15 minutes


# --- Endpoints ---


@router.get("/monitoring/kpis")
async def monitoring_kpis():
    conn = get_db()
    try:
        active_coas = conn.execute("SELECT COUNT(*) FROM coas WHERE status IN ('approved', 'executing')").fetchone()[0]
        total_coas = conn.execute("SELECT COUNT(*) FROM coas").fetchone()[0]
        total_activity = conn.execute("SELECT COUNT(*) FROM activity_log").fetchone()[0]
        active_injects = conn.execute("SELECT COUNT(*) FROM injects WHERE status = 'delivered'").fetchone()[0]
        last_event_row = conn.execute("SELECT timestamp FROM activity_log ORDER BY id DESC LIMIT 1").fetchone()
        last_event = last_event_row["timestamp"] if last_event_row else None
        total_briefings = conn.execute("SELECT COUNT(*) FROM briefings").fetchone()[0]
    finally:
        conn.close()
    return {
        "active_coas": active_coas,
        "total_coas": total_coas,
        "total_activity": total_activity,
        "active_injects": active_injects,
        "last_event": last_event,
        "total_briefings": total_briefings,
    }


@router.get("/monitoring/activity")
async def monitoring_activity(limit: int = 50):
    conn = get_db()
    try:
        rows = conn.execute("SELECT * FROM activity_log ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    finally:
        conn.close()
    return [row_to_activity(r) for r in rows]


@router.get("/monitoring/map-data")
async def monitoring_map_data():
    zones = list(_MONITORING_ZONES)
    # Add markers for COA target entities
    conn = get_db()
    try:
        rows = conn.execute("SELECT name, target_entities, status FROM coas WHERE status NOT IN ('assessed')").fetchall()
    finally:
        conn.close()
    seen_coords: set[tuple[float, float]] = set()
    for row in rows:
        entities = json.loads(row["target_entities"]) if row["target_entities"] else []
        for entity in entities:
            key = entity.lower().strip()
            for geo_key, (lat, lon) in _ENTITY_GEO.items():
                if geo_key in key:
                    if (lat, lon) not in seen_coords:
                        seen_coords.add((lat, lon))
                        zones.append({
                            "lat": lat, "lon": lon,
                            "label": entity,
                            "type": "coa_target",
                            "status": row["status"],
                        })
                    break
    return zones


@router.get("/monitoring/macro")
async def get_monitoring_macro():
    """Return key macro indicators for the monitoring dashboard.

    Pulls latest Brent crude from FRED and USD/CNY from yfinance.
    Returns summary values + recent sparkline data (last 10 observations).
    Cached for 15 minutes to respect FRED rate limits.
    """
    import time as _time
    now = _time.time()
    if _macro_cache["data"] is not None and (now - _macro_cache["fetched_at"]) < _MACRO_CACHE_TTL:
        return _macro_cache["data"]

    result: dict[str, Any] = {
        "usd_cny": None,
        "brent": None,
        "vix": None,
        "dxy": None,
        "sparklines": {
            "capital_flight": [],
            "currency_vol": [],
            "equity_swap": [],
        },
    }

    # --- All macro data via yfinance (real-time, no FRED publication lag) ---
    try:
        yf = YFinanceClient()

        # Brent crude futures (matches publicly-reported ~$100/bbl, not FRED's stale ~$120)
        try:
            brent_ticker = await asyncio.to_thread(_yf_ticker_history, "BZ=F", "15d")
            if brent_ticker is not None and len(brent_ticker) > 0:
                closes = [float(c) for c in brent_ticker["Close"].tolist() if c == c]
                if closes:
                    result["brent"] = round(closes[-1], 2)
                    result["sparklines"]["capital_flight"] = [round(c, 2) for c in closes[-10:]]
        except Exception as exc:
            logger.warning("yfinance Brent fetch failed: %s", exc)

        # VIX
        try:
            vix_ticker = await asyncio.to_thread(_yf_ticker_history, "^VIX", "15d")
            if vix_ticker is not None and len(vix_ticker) > 0:
                closes = [float(c) for c in vix_ticker["Close"].tolist() if c == c]
                if closes:
                    result["vix"] = round(closes[-1], 2)
                    result["sparklines"]["currency_vol"] = [round(c, 2) for c in closes[-10:]]
        except Exception as exc:
            logger.warning("yfinance VIX fetch failed: %s", exc)

        # ICE DXY (matches the publicly-reported ~98 value, not FRED's broad index)
        try:
            dxy_ticker = await asyncio.to_thread(_yf_ticker_history, "DX-Y.NYB", "10d")
            if dxy_ticker is not None and len(dxy_ticker) > 0:
                closes = [float(c) for c in dxy_ticker["Close"].tolist() if c == c]  # filter NaN
                if closes:
                    result["dxy"] = round(closes[-1], 2)
                    result["sparklines"]["equity_swap"] = [round(c, 2) for c in closes[-10:]]
        except Exception as exc:
            logger.warning("yfinance DXY fetch failed: %s", exc)

        # USD/CNY
        info = await yf.get_info("CNY=X")
        price = info.get("regularMarketPrice") or info.get("previousClose")
        if price:
            result["usd_cny"] = round(float(price), 3)
    except Exception as exc:
        logger.warning("yfinance fetch failed: %s", exc)

    _macro_cache["data"] = result
    _macro_cache["fetched_at"] = now
    return result
