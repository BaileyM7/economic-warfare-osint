"""Risk Feed router — proactive card-style surfacing of risk shifts.

Exposes:
- GET  /api/risk-feed              -> current in-memory feed items
- POST /api/risk-feed/refresh      -> rebuild the feed from configured sources

Mode is controlled by env var RISK_FEED_MODE:
- "fixture" : load only from fixtures/risk_feed_demo.json (offline-safe)
- "live"    : pull from OFAC delta + GDELT/yfinance only
- "auto"    : try live; if it returns nothing or errors, fall back to fixture

The feed is intentionally process-local — POC scope. Persistence to a
feed_items table is a Phase 2 concern.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException

from src.db import log_activity
from src.risk_feed.enrich import enrich_payload
from src.routers._shared import notify_monitoring
from src.tools.markets.feed import build_markets_feed
from src.tools.sanctions.delta import detect_ofac_delta

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/risk-feed", tags=["risk-feed"])

# Severity ordering for sort
_SEVERITY_RANK = {"high": 0, "medium": 1, "low": 2, "info": 3}

# In-memory store. Lifetime = process lifetime.
_FEED_ITEMS: list[dict[str, Any]] = []
_LAST_REFRESH: dict[str, Any] = {"at": None, "source": None, "count": 0, "errors": []}

_FIXTURE_PATH = Path("fixtures/risk_feed_demo.json")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _load_fixture() -> list[dict[str, Any]]:
    if not _FIXTURE_PATH.exists():
        logger.warning("Risk feed fixture not found at %s", _FIXTURE_PATH)
        return []
    try:
        data = json.loads(_FIXTURE_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("Could not load risk feed fixture: %s", exc)
        return []
    items = data.get("items") or []
    return [dict(i) for i in items if isinstance(i, dict)]


def _sort_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        items,
        key=lambda it: (
            _SEVERITY_RANK.get(it.get("severity", "info"), 99),
            it.get("category", ""),
            it.get("fetched_at", ""),
        ),
    )


def _dedupe(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for it in items:
        item_id = it.get("id")
        if not item_id or item_id in seen:
            continue
        seen.add(item_id)
        out.append(it)
    return out


async def _build_live_feed() -> tuple[list[dict[str, Any]], list[str]]:
    """Build the live feed in parallel from all configured sources.

    Returns (items, errors). Errors are non-fatal; one source failing should
    not blank the whole feed.
    """
    import asyncio

    errors: list[str] = []

    async def _safe_ofac() -> list[dict[str, Any]]:
        try:
            return await detect_ofac_delta()
        except Exception as exc:
            logger.warning("OFAC delta failed: %s", exc)
            errors.append(f"ofac: {type(exc).__name__}: {exc}")
            return []

    async def _safe_markets() -> list[dict[str, Any]]:
        try:
            return await build_markets_feed()
        except Exception as exc:
            logger.warning("Markets feed failed: %s", exc)
            errors.append(f"markets: {type(exc).__name__}: {exc}")
            return []

    ofac_items, market_items = await asyncio.gather(_safe_ofac(), _safe_markets())
    return ofac_items + market_items, errors


def _category_counts(items: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for it in items:
        cat = it.get("category", "other")
        counts[cat] = counts.get(cat, 0) + 1
    return counts


@router.get("")
async def get_risk_feed():
    """Return the current in-memory feed plus metadata about the last refresh."""
    return {
        "items": _FEED_ITEMS,
        "last_refresh": _LAST_REFRESH,
        "category_counts": _category_counts(_FEED_ITEMS),
    }


@router.post("/refresh")
async def refresh_risk_feed():
    """Rebuild the feed from configured sources. Synchronous so the caller
    sees the new items immediately.
    """
    mode = (os.getenv("RISK_FEED_MODE") or "auto").strip().lower()
    items: list[dict[str, Any]] = []
    errors: list[str] = []
    source_used = mode

    if mode == "fixture":
        items = _load_fixture()
        source_used = "fixture"
    elif mode == "live":
        items, errors = await _build_live_feed()
        source_used = "live"
    else:  # "auto"
        live_items, errors = await _build_live_feed()
        if live_items:
            items = live_items
            source_used = "live"
        else:
            logger.info("Risk feed auto mode: live yielded 0 items, using fixture")
            items = _load_fixture()
            source_used = "fixture"

    items = _sort_items(_dedupe(items))

    global _FEED_ITEMS, _LAST_REFRESH
    _FEED_ITEMS = items
    _LAST_REFRESH = {
        "at": _now_iso(),
        "source": source_used,
        "count": len(items),
        "errors": errors,
    }

    counts = _category_counts(items)
    counts_str = ", ".join(f"{k}={v}" for k, v in counts.items()) or "none"
    log_activity(
        event_type="risk_feed_refreshed",
        message=f"Risk feed refresh ({source_used}): {len(items)} items ({counts_str})",
        source="monitor",
        severity="info",
    )
    await notify_monitoring(
        "risk_feed_refreshed",
        f"Risk feed refresh ({source_used}): {len(items)} items",
    )

    # Per-item activity rows give the monitoring page the "watching" beat.
    for it in items[:25]:  # cap to avoid flooding activity_log on big refreshes
        log_activity(
            event_type="risk_card_generated",
            message=f"[{it.get('category')}] {it.get('headline')}",
            source="monitor",
            severity=it.get("severity", "info"),
            related_id=it.get("id"),
        )

    return {
        "items": items,
        "last_refresh": _LAST_REFRESH,
        "category_counts": counts,
    }


@router.get("/{item_id}")
async def get_risk_feed_item(item_id: str):
    """Return one feed item by id. Used by the frontend to fetch the
    synthetic_payload before triggering /api/coa/generate.
    """
    for it in _FEED_ITEMS:
        if it.get("id") == item_id:
            return it
    raise HTTPException(status_code=404, detail="risk feed item not found")


@router.post("/{item_id}/prepare-coa")
async def prepare_coa_payload(item_id: str):
    """Enrich the feed item's synthetic_payload with extra sources fetched
    in parallel from cheap cached helpers (GDELT / CSL / PEP / OFAC / yfinance
    profile) and return the augmented payload.

    Best-effort: any enrichment failure falls back to the un-enriched payload.
    Adds a few seconds of latency on first click; subsequent clicks on the
    same card hit the diskcache and return near-instantly.
    """
    target: dict[str, Any] | None = None
    for it in _FEED_ITEMS:
        if it.get("id") == item_id:
            target = it
            break
    if target is None:
        raise HTTPException(status_code=404, detail="risk feed item not found")

    base_payload = dict(target.get("synthetic_payload") or {})
    base_count = len(base_payload.get("sources_used") or [])
    try:
        enriched = await enrich_payload(target)
    except Exception as exc:
        logger.warning("prepare_coa_payload enrichment failed for %s: %s", item_id, exc)
        enriched = base_payload

    enriched_count = len(enriched.get("sources_used") or [])
    log_activity(
        event_type="risk_card_enriched",
        message=(
            f"[{target.get('category')}] {target.get('headline')} — "
            f"sources {base_count} → {enriched_count}"
        ),
        source="monitor",
        severity="info",
        related_id=item_id,
    )

    return {
        "item_id": item_id,
        "synthetic_payload": enriched,
        "sources_added": max(0, enriched_count - base_count),
    }
