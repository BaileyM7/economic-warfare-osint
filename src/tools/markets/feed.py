"""GDELT + yfinance + entity-watch composite feed builder for the Risk Feed POC.

Three signal types are merged:

1. yfinance large-move detector — for a small watch-list of defense /
   semiconductor / energy tickers, flags |1d % change| >= MOVE_THRESHOLD.
2. GDELT bilateral / regional tone-shift detector — for hot regions
   (Strait of Hormuz, Taiwan Strait, etc.).
3. **Entity-watch GDELT scan** (new) — for a curated list of named
   entities, pulls the most negative-tone recent article and surfaces it
   if tone is below threshold. Each watch-entry is typed with a target
   category so the resulting card lands in company_sanctions /
   people_sanctions / markets as appropriate.

The watch-lists are short and POC-shaped on purpose. They're the right place
for the operator to inject domain knowledge — the rest of the pipeline is
source-agnostic.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

from src.tools.geopolitical.client import gdelt_doc_search
from src.tools.market.client import YFinanceClient

logger = logging.getLogger(__name__)

# --- Suggested starter watch-lists ---
#
# These were the original hardcoded watch-lists driving every user's feed.
# After Phase 3 (per-user saved watch-lists), they are no longer the source
# of truth — every user's feed is built from their own watchlist_items rows.
# We keep them here purely as content for the frontend's "Suggested starter
# items" tile (always visible inside the watch-list manager).
#
# Curated to hit exactly **10 suggestions per category** so each column on
# the picker UI has a balanced starter set:
#
#   markets           = SUGGESTED_TICKERS (5) + SUGGESTED_GDELT_REGIONS (3)
#                       + SUGGESTED_ENTITIES filtered to category='markets' (2)
#                       = 10
#   company_sanctions = SUGGESTED_ENTITIES filtered to that category    = 10
#   people_sanctions  = SUGGESTED_ENTITIES filtered to that category    = 10
#
# Keep these counts aligned when editing or the suggestion grid will look
# lopsided.

SUGGESTED_TICKERS: list[tuple[str, str]] = [
    ("LMT", "Lockheed Martin Corporation"),
    ("RTX", "RTX Corporation"),
    ("ASML", "ASML Holding NV"),
    ("TSM", "Taiwan Semiconductor Manufacturing"),
    ("BZ=F", "Brent Crude"),
]

SUGGESTED_GDELT_REGIONS: list[tuple[str, str]] = [
    ("Strait of Hormuz", "Strait of Hormuz shipping lanes"),
    ("Taiwan Strait", "Taiwan Strait military activity"),
    ("Red Sea Houthi", "Red Sea / Bab el-Mandeb shipping"),
]

# Each tuple: (gdelt_query, display_label, category).
# 10 company_sanctions + 10 people_sanctions + 2 markets = 22 total.
SUGGESTED_ENTITIES: list[tuple[str, str, str]] = [
    # --- Company sanctions (10) ---
    ("Huawei sanctions", "Huawei Technologies", "company_sanctions"),
    ("SMIC sanctions", "SMIC (Semiconductor Manufacturing International)", "company_sanctions"),
    ("ZTE Corporation sanctions", "ZTE Corporation", "company_sanctions"),
    ("Rosneft sanctions", "Rosneft", "company_sanctions"),
    ("Sinopec", "Sinopec", "company_sanctions"),
    ("Lukoil sanctions", "Lukoil", "company_sanctions"),
    ("Wagner Group sanctions", "Wagner Group / Africa Corps", "company_sanctions"),
    ("NSO Group", "NSO Group", "company_sanctions"),
    ("Mahan Air sanctions", "Mahan Air", "company_sanctions"),
    ("Volga-Dnepr sanctions", "Volga-Dnepr Group", "company_sanctions"),
    # --- People sanctions (10) ---
    ("Yevgeny Prigozhin network", "Yevgeny Prigozhin network", "people_sanctions"),
    ("IRGC senior commanders", "IRGC senior commanders", "people_sanctions"),
    ("Ramzan Kadyrov", "Ramzan Kadyrov", "people_sanctions"),
    ("Roman Abramovich", "Roman Abramovich", "people_sanctions"),
    ("Alisher Usmanov", "Alisher Usmanov", "people_sanctions"),
    ("Igor Sechin", "Igor Sechin", "people_sanctions"),
    ("Hassan Nasrallah successors", "Hezbollah leadership", "people_sanctions"),
    ("Kim Jong Un", "Kim Jong Un inner circle", "people_sanctions"),
    ("Bashar al-Assad regime", "Bashar al-Assad regime", "people_sanctions"),
    ("Nicolas Maduro inner circle", "Nicolás Maduro inner circle", "people_sanctions"),
    # --- Markets (2; rounds out the markets column to 10 alongside tickers/regions) ---
    ("rare earth export curbs", "Rare earth export controls", "markets"),
    ("semiconductor export controls China", "Semiconductor export controls", "markets"),
]

# Thresholds
MOVE_THRESHOLD_PCT = 2.5
TONE_THRESHOLD = -3.0  # GDELT avg_tone roughly -10..+10


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _severity_from_move(pct: float) -> str:
    abs_pct = abs(pct)
    if abs_pct >= 5.0:
        return "high"
    if abs_pct >= 3.0:
        return "medium"
    return "low"


def _severity_from_tone(tone: float) -> str:
    if tone <= -5.0:
        return "high"
    if tone <= -3.5:
        return "medium"
    return "low"


def _yfinance_move_to_item(
    ticker: str, name: str, pct_change: float, current: float | None
) -> dict[str, Any]:
    direction = "up" if pct_change > 0 else "down"
    headline = f"{name} ({ticker}) {direction} {abs(pct_change):.1f}% on the session"
    # yfinance moves describe today's session — use UTC date as event_at proxy
    # (close enough for a card; no need for tz lookup since cards are coarse-grained).
    event_date = datetime.now(timezone.utc).date().isoformat()
    findings = [f"Intraday move: {'+' if pct_change > 0 else ''}{pct_change:.2f}%"]
    if current is not None:
        findings.append(f"Last price: {current:.2f}")

    payload = {
        "scenario_type": "market_shift",
        "executive_summary": (
            f"{name} ({ticker}) moved {pct_change:+.2f}% intraday. "
            "Magnitude exceeds the watch-list threshold and warrants a defensive review of "
            "exposure, supplier dependencies, and any pending contract milestones."
        ),
        "target_entities": [name],
        "key_findings": findings,
        "sources_used": [
            {
                "name": f"yfinance — {ticker}",
                "url": f"https://finance.yahoo.com/quote/{ticker}",
                "description": f"Intraday quote and history for {ticker}",
            }
        ],
        "confidence": 0.7,
    }

    return {
        "id": f"yf-{ticker}-{datetime.now(timezone.utc).strftime('%Y%m%d')}",
        "category": "markets",
        "severity": _severity_from_move(pct_change),
        "headline": headline,
        "entity": name,
        "source_url": f"https://finance.yahoo.com/quote/{ticker}",
        "event_at": event_date,
        "fetched_at": _now_iso(),
        "synthetic_payload": payload,
    }


def _gdelt_tone_band(t: float) -> str:
    if t <= -5.0:
        return "strongly negative"
    if t <= -3.5:
        return "negative"
    if t <= -2.0:
        return "moderately negative"
    if t < 0:
        return "mildly negative"
    return "neutral or positive"


def _gdelt_articles_to_sources(
    query: str, label: str, events: list[Any], tone_known: bool
) -> list[dict[str, Any]]:
    """Turn a list of GdeltEvent into sources_used entries (deduped by URL)."""
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for ev in events:
        url = getattr(ev, "source_url", "") or ""
        if not url or url in seen:
            continue
        seen.add(url)
        tone = getattr(ev, "avg_tone", None)
        if tone_known and tone is not None:
            desc = f"GDELT 2.0 article on '{query}', sentiment {_gdelt_tone_band(tone)}"
        else:
            desc = f"GDELT 2.0 article on '{query}'"
        out.append(
            {
                "name": f"GDELT article — {label}",
                "url": url,
                "description": desc,
            }
        )
    return out


def _gdelt_event_to_item_region(
    query: str, label: str, primary: Any, extra_events: list[Any] | None = None
) -> dict[str, Any] | None:
    tone = getattr(primary, "avg_tone", None)
    url = getattr(primary, "source_url", "")
    if tone is None or not url:
        return None

    band = _gdelt_tone_band(tone)
    headline = f"{label}: {band} coverage detected"

    # Build sources: primary first, then up to 2 distinct backups.
    all_events = [primary] + [e for e in (extra_events or []) if e is not primary]
    sources = _gdelt_articles_to_sources(query, label, all_events, tone_known=True)[:3]

    payload = {
        "scenario_type": "geopolitical_event",
        "executive_summary": (
            f"GDELT surfaced {band} coverage about {label} across {len(sources)} article(s). "
            "Pair with operational context before drawing conclusions."
        ),
        "target_entities": [label],
        "key_findings": [
            f"Article sentiment band: {band}",
            f"Articles surfaced: {len(sources)}",
            f"Query: {query}",
        ],
        "sources_used": sources,
        "confidence": 0.65,
    }

    primary_date = getattr(primary, "date", None)
    event_at = primary_date.date().isoformat() if primary_date is not None else None

    return {
        "id": f"gdelt-region-{abs(hash((query, url))) % 10_000_000}",
        "category": "markets",
        "severity": _severity_from_tone(tone),
        "headline": headline,
        "entity": label,
        "source_url": url,
        "event_at": event_at,
        "fetched_at": _now_iso(),
        "synthetic_payload": payload,
    }


def _gdelt_event_to_item_entity(
    query: str,
    label: str,
    category: str,
    primary: Any,
    extra_events: list[Any],
    article_count: int,
    tone_known: bool,
) -> dict[str, Any] | None:
    """Build an entity-watch card. When ``tone_known`` is False the card is
    surfaced on volume alone and the headline/payload do not mention a tone
    value — we never invent one."""
    tone = getattr(primary, "avg_tone", None) if tone_known else None
    url = getattr(primary, "source_url", "")
    if not url:
        return None

    all_events = [primary] + [e for e in extra_events if e is not primary]
    sources = _gdelt_articles_to_sources(query, label, all_events, tone_known=tone_known)[:3]

    if tone_known and tone is not None:
        band = _gdelt_tone_band(tone)
        headline = f"{label}: {article_count} articles in 7d, worst tone {band}"
        severity = _severity_from_tone(tone)
        summary = (
            f"GDELT returned {article_count} articles about {label} in the last 7 days; "
            f"the most negative article was rated {band}. "
            f"{len(sources)} representative articles attached as sources for cross-reference. "
            "Combination of volume and negative sentiment suggests an active news cycle worth investigating."
        )
        finding_lines = [
            f"Worst-article sentiment band: {band}",
            f"Article volume in 7d window: {article_count}",
            f"Sources attached: {len(sources)}",
            f"Query: {query}",
        ]
    else:
        headline = f"{label}: elevated article volume ({article_count} in 7d)"
        severity = "medium" if article_count >= 12 else "low"
        summary = (
            f"GDELT returned {article_count} articles about {label} in the last 7 days. "
            "Per-article sentiment was unavailable for this query, so this card is "
            f"surfaced on volume alone; {len(sources)} representative articles attached for cross-reference."
        )
        finding_lines = [
            f"Article volume in 7d window: {article_count}",
            "Per-article sentiment: not available for this query",
            f"Sources attached: {len(sources)}",
            f"Query: {query}",
        ]

    if category == "people_sanctions":
        scenario = "designated_individual_activity"
    elif category == "company_sanctions":
        scenario = "company_under_pressure"
    else:
        scenario = "market_shift"

    payload = {
        "scenario_type": scenario,
        "executive_summary": summary,
        "target_entities": [label],
        "key_findings": finding_lines,
        "sources_used": sources,
        "confidence": 0.7 if tone_known else 0.55,
    }

    primary_date = getattr(primary, "date", None)
    event_at = primary_date.date().isoformat() if primary_date is not None else None

    return {
        "id": f"gdelt-ent-{abs(hash((query, url))) % 10_000_000}",
        "category": category,
        "severity": severity,
        "headline": headline,
        "entity": label,
        "source_url": url,
        "event_at": event_at,
        "fetched_at": _now_iso(),
        "synthetic_payload": payload,
    }


async def _fetch_ticker_move(yf: YFinanceClient, ticker: str, name: str) -> dict[str, Any] | None:
    try:
        price = await yf.get_price_data(ticker, period="5d")
    except Exception as exc:
        logger.warning("yfinance fetch failed for %s: %s", ticker, exc)
        return None

    pct = getattr(price, "change_pct", None)
    current = getattr(price, "current_price", None)

    if pct is None:
        hist = getattr(price, "historical", None) or []
        if len(hist) >= 2:
            try:
                first = float(hist[0].close)
                last = float(hist[-1].close)
                if first:
                    pct = (last - first) / first * 100.0
            except (AttributeError, TypeError, ValueError):
                pct = None

    if pct is None:
        return None
    if abs(pct) < MOVE_THRESHOLD_PCT:
        return None
    return _yfinance_move_to_item(ticker, name, float(pct), current)


async def _fetch_region_signal(query: str, label: str) -> dict[str, Any] | None:
    try:
        events = await gdelt_doc_search(query, days=3, max_records=25)
    except Exception as exc:
        logger.warning("GDELT region search failed for %s: %s", query, exc)
        return None
    if not events:
        return None
    scored = [e for e in events if getattr(e, "avg_tone", None) is not None]
    if not scored:
        return None
    # Order by worst tone first; pass top 3 worst-tone hits as sources.
    sorted_by_tone = sorted(scored, key=lambda e: e.avg_tone or 0.0)
    worst = sorted_by_tone[0]
    if (worst.avg_tone or 0.0) > TONE_THRESHOLD:
        return None
    backups = sorted_by_tone[1:3]
    return _gdelt_event_to_item_region(query, label, worst, extra_events=backups)


async def _fetch_entity_signal(query: str, label: str, category: str) -> dict[str, Any] | None:
    try:
        events = await gdelt_doc_search(query, days=7, max_records=25)
    except Exception as exc:
        logger.warning("GDELT entity search failed for %s: %s", query, exc)
        return None
    if not events:
        return None

    article_count = len(events)
    scored = [e for e in events if getattr(e, "avg_tone", None) is not None]

    if scored:
        sorted_by_tone = sorted(scored, key=lambda e: e.avg_tone or 0.0)
        worst = sorted_by_tone[0]
        # Surface if either tone is bad enough, or volume is high enough.
        if (worst.avg_tone or 0.0) > TONE_THRESHOLD and article_count < 10:
            return None
        backups = sorted_by_tone[1:3]
        return _gdelt_event_to_item_entity(
            query, label, category, worst, backups, article_count, tone_known=True
        )

    # No tone data; fall back to a volume-only card with honest labeling.
    if article_count < 8:
        return None
    representative = events[0]
    backups = events[1:3]
    return _gdelt_event_to_item_entity(
        query, label, category, representative, backups, article_count, tone_known=False
    )


async def build_markets_feed(
    tickers: list[tuple[str, str]] | None = None,
    regions: list[tuple[str, str]] | None = None,
    entities: list[tuple[str, str, str]] | None = None,
) -> list[dict[str, Any]]:
    """Run all signal builders in parallel; return de-duped feed items.

    Despite the historical name, this builder produces cards for ALL three
    feed categories (markets / company_sanctions / people_sanctions) via the
    entity-watch scan. The risk_feed router merges these with sanctions feed
    items.

    Each parameter mirrors the shape of the corresponding SUGGESTED_*
    constant in this module. Empty list = no fan-out for that signal type
    (legitimate when the user hasn't added any of that kind to their
    watch-list yet). None means "use SUGGESTED_*" — back-compat for any
    caller that hasn't migrated to per-user watch-lists yet.
    """
    tickers = tickers if tickers is not None else SUGGESTED_TICKERS
    regions = regions if regions is not None else SUGGESTED_GDELT_REGIONS
    entities = entities if entities is not None else SUGGESTED_ENTITIES

    yf = YFinanceClient()
    yf_tasks = [_fetch_ticker_move(yf, t, name) for t, name in tickers]
    region_tasks = [_fetch_region_signal(q, label) for q, label in regions]
    entity_tasks = [_fetch_entity_signal(q, label, cat) for q, label, cat in entities]

    results = await asyncio.gather(*yf_tasks, *region_tasks, *entity_tasks, return_exceptions=True)

    items: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for r in results:
        if isinstance(r, Exception):
            logger.warning("Markets feed task raised: %s", r)
            continue
        if not r:
            continue
        if r["id"] in seen_ids:
            continue
        seen_ids.add(r["id"])
        items.append(r)

    return items
