"""Risk Feed router — proactive card-style surfacing of risk shifts.

After Phase 3 every endpoint is per-user: each authenticated caller gets their
own feed driven by their own `watchlist_items` rows (see src/routers/watchlist.py).
Two users hitting refresh at the same time see two independent feeds.

Endpoints:
- GET  /api/risk-feed                       -> caller's current in-memory feed
- POST /api/risk-feed/refresh               -> rebuild the caller's feed from sources
- GET  /api/risk-feed/{item_id}             -> fetch one item from the caller's feed
- POST /api/risk-feed/{item_id}/prepare-coa -> click-time source enrichment

Mode is controlled by env var RISK_FEED_MODE:
- "fixture" : load only from fixtures/risk_feed_demo.json (offline-safe, identical for every user)
- "live"    : pull from OFAC ranked surface + watch-list-driven markets/CSL feeds
- "auto"    : try live; if it returns nothing or errors, fall back to fixture

Storage is intentionally process-local (in-memory dict keyed by username).
Persistence to a `feed_items` table is a Phase 3.5 concern.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from cachetools import TTLCache
from fastapi import APIRouter, Depends, HTTPException

from src.auth import require_auth
from src.db import log_activity
from src.risk_feed.enrich import enrich_payload
from src.routers._shared import notify_monitoring
from src.routers.watchlist import load_active_items_for_user
from src.tools.markets.feed import build_markets_feed
from src.tools.sanctions.delta import build_sanctions_feed

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/risk-feed", tags=["risk-feed"])

# Severity ordering for sort
_SEVERITY_RANK = {"high": 0, "medium": 1, "low": 2, "info": 3}

# Per-user in-memory store. Lifetime = process lifetime.
# Bounded so an idle user's feed snapshot doesn't pin yfinance + GDELT
# payloads forever on the 512 MB Render instance.
# Phase 3.5 will replace this with a `feed_items` SQL table.
_FEED_MAX_USERS = 100
_FEED_TTL_SEC = 1800
_FEED_ITEMS: TTLCache[str, list[dict[str, Any]]] = TTLCache(
    maxsize=_FEED_MAX_USERS, ttl=_FEED_TTL_SEC
)
_LAST_REFRESH: TTLCache[str, dict[str, Any]] = TTLCache(maxsize=_FEED_MAX_USERS, ttl=_FEED_TTL_SEC)

_EMPTY_REFRESH_META: dict[str, Any] = {
    "at": None,
    "source": None,
    "count": 0,
    "errors": [],
}

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


def _watchlist_to_feed_args(grouped: dict[str, list[dict]]) -> dict[str, Any]:
    """Translate a user's watch-list (grouped by entity_kind) into the kwargs
    that build_markets_feed / build_sanctions_feed expect.

    Empty lists are passed explicitly so the builder runs zero fan-out for a
    signal type the user hasn't subscribed to (rather than falling back to
    SUGGESTED_*).
    """
    tickers: list[tuple[str, str]] = [
        (row["query"], row["label"]) for row in grouped.get("ticker", [])
    ]
    regions: list[tuple[str, str]] = [
        (row["query"], row["label"]) for row in grouped.get("gdelt_region", [])
    ]
    entities: list[tuple[str, str, str]] = [
        (row["query"], row["label"], row.get("category", "markets"))
        for row in grouped.get("gdelt_query", [])
    ]
    csl_keywords: list[str] = [row["query"] for row in grouped.get("sanctions_keyword", [])]
    return {
        "tickers": tickers,
        "regions": regions,
        "entities": entities,
        "csl_keywords": csl_keywords,
    }


async def _build_live_feed_for_user(username: str) -> tuple[list[dict[str, Any]], list[str]]:
    """Build the live feed for *username* in parallel from configured sources.

    The OFAC ranked surface (top recent + program-weighted SDN designations)
    runs unconditionally — it's a global signal that matters even when the
    user hasn't curated a watch-list yet. The markets fan-out and CSL keyword
    augmentation are driven by the user's watch-list rows.
    """
    import asyncio

    errors: list[str] = []
    grouped = load_active_items_for_user(username)
    args = _watchlist_to_feed_args(grouped)

    async def _safe_sanctions() -> list[dict[str, Any]]:
        try:
            return await build_sanctions_feed(csl_keywords=args["csl_keywords"])
        except Exception as exc:
            logger.warning("Sanctions feed failed for %s: %s", username, exc)
            errors.append(f"sanctions: {type(exc).__name__}: {exc}")
            return []

    async def _safe_markets() -> list[dict[str, Any]]:
        try:
            return await build_markets_feed(
                tickers=args["tickers"],
                regions=args["regions"],
                entities=args["entities"],
            )
        except Exception as exc:
            logger.warning("Markets feed failed for %s: %s", username, exc)
            errors.append(f"markets: {type(exc).__name__}: {exc}")
            return []

    sanctions_items, market_items = await asyncio.gather(_safe_sanctions(), _safe_markets())
    return sanctions_items + market_items, errors


def _category_counts(items: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for it in items:
        cat = it.get("category", "other")
        counts[cat] = counts.get(cat, 0) + 1
    return counts


@router.get("")
async def get_risk_feed(username: str = Depends(require_auth)):
    """Return the calling user's current in-memory feed + last-refresh metadata."""
    items = _FEED_ITEMS.get(username, [])
    return {
        "items": items,
        "last_refresh": _LAST_REFRESH.get(username, dict(_EMPTY_REFRESH_META)),
        "category_counts": _category_counts(items),
    }


@router.post("/refresh")
async def refresh_risk_feed(username: str = Depends(require_auth)):
    """Rebuild *this user's* feed from configured sources. Synchronous so the
    caller sees the new items immediately."""
    mode = (os.getenv("RISK_FEED_MODE") or "auto").strip().lower()
    items: list[dict[str, Any]] = []
    errors: list[str] = []
    source_used = mode

    if mode == "fixture":
        items = _load_fixture()
        source_used = "fixture"
    elif mode == "live":
        items, errors = await _build_live_feed_for_user(username)
        source_used = "live"
    else:  # "auto"
        live_items, errors = await _build_live_feed_for_user(username)
        if live_items:
            items = live_items
            source_used = "live"
        else:
            logger.info("Risk feed auto mode: live yielded 0 items for %s, using fixture", username)
            items = _load_fixture()
            source_used = "fixture"

    items = _sort_items(_dedupe(items))

    _FEED_ITEMS[username] = items
    last_refresh = {
        "at": _now_iso(),
        "source": source_used,
        "count": len(items),
        "errors": errors,
    }
    _LAST_REFRESH[username] = last_refresh

    counts = _category_counts(items)
    counts_str = ", ".join(f"{k}={v}" for k, v in counts.items()) or "none"
    log_activity(
        event_type="risk_feed_refreshed",
        message=f"Risk feed refresh ({source_used}) for {username}: {len(items)} items ({counts_str})",
        source=username,
        severity="info",
    )
    await notify_monitoring(
        "risk_feed_refreshed",
        f"Risk feed refresh ({source_used}) for {username}: {len(items)} items",
    )

    # Per-item activity rows give the monitoring page the "watching" beat.
    for it in items[:25]:  # cap to avoid flooding activity_log on big refreshes
        log_activity(
            event_type="risk_card_generated",
            message=f"[{it.get('category')}] {it.get('headline')}",
            source=username,
            severity=it.get("severity", "info"),
            related_id=it.get("id"),
        )

    return {
        "items": items,
        "last_refresh": last_refresh,
        "category_counts": counts,
    }


@router.get("/{item_id}")
async def get_risk_feed_item(item_id: str, username: str = Depends(require_auth)):
    """Return one feed item from the calling user's feed."""
    for it in _FEED_ITEMS.get(username, []):
        if it.get("id") == item_id:
            return it
    raise HTTPException(status_code=404, detail="risk feed item not found")


@router.post("/{item_id}/prepare-coa")
async def prepare_coa_payload(item_id: str, username: str = Depends(require_auth)):
    """Enrich the feed item's synthetic_payload with extra sources fetched
    in parallel from cheap cached helpers (GDELT / CSL / PEP / OFAC / yfinance
    profile) and return the augmented payload.

    Best-effort: any enrichment failure falls back to the un-enriched payload.
    Adds a few seconds of latency on first click; subsequent clicks on the
    same card hit the diskcache and return near-instantly.
    """
    target: dict[str, Any] | None = None
    for it in _FEED_ITEMS.get(username, []):
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
        source=username,
        severity="info",
        related_id=item_id,
    )

    return {
        "item_id": item_id,
        "synthetic_payload": enriched,
        "sources_added": max(0, enriched_count - base_count),
    }
