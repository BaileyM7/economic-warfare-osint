"""Dynamic comparable event sourcing for the sanctions impact projection model.

Three-layer pipeline:
  1. Cache check  (7-day TTL, keyed on sector + sanction_type + country)
  2. Claude suggestion + yfinance historical validation
  3. Static fallback (SANCTIONS_COMPARABLES passed in by caller)

Returns (validated_list, source_label) where source_label is one of
"cache" | "claude" | "static_fallback".
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import datetime, timedelta
from typing import Any

import anthropic
import yfinance as yf

from .common.cache import get_cached, set_cached
from .common.config import config

logger = logging.getLogger(__name__)

_CACHE_NS = "comparable_sourcer"
_CACHE_TTL = 7 * 24 * 3600  # 7 days

# Tickers that are obviously not real exchange symbols — reject before hitting yfinance.
_INVALID_TICKERS = frozenset({
    "UNLISTED", "N/A", "NA", "PRIVATE", "UNKNOWN", "TBD", "NONE",
    "DELISTED", "OTC", "OTCPK", "N/L", "-", ".", "NULL", "NIL",
})

# Valid ticker pattern: 1–15 chars, letters/digits plus the separators used by
# major exchanges (dot for LSE/HK/EU, hyphen for NYSE preferred shares, caret for indices).
_TICKER_RE = re.compile(r'^[A-Za-z0-9.\-\^]{1,15}$')

_SUGGEST_PROMPT = """\
You are a financial historian specializing in economic sanctions and export controls.

Given: sector="{sector}", sanction_type="{sanction_type}", target_country="{country}"

List up to 10 real historical cases where a PUBLICLY TRADED company in the same or a closely \
related sector experienced a materially similar regulatory or sanctions shock. Only include events \
that actually occurred and caused a measurable stock price reaction.

IMPORTANT RULES:
- Only include companies that were actively listed on a public exchange at the time of the event.
- If a company was private, state-owned without a public listing, or had no ADR/GDR, omit it entirely.
- Do NOT use placeholder values like "UNLISTED", "N/A", "PRIVATE", or similar — if you cannot identify \
a real ticker, omit the case.
- Ticker must be the actual exchange symbol (e.g. "BABA", "0763.HK", "GAZP.ME") — no $ prefix.

For each case provide:
- name: full company name
- ticker: exchange symbol — no $ prefix, no placeholder text
- sanction_date: the date the shock became public (YYYY-MM-DD)
- sanction_type: one of ofac_ccmc, us_export_control, sectoral, swift_cutoff, retaliation, bis_penalty
- sector: one word — tech, semiconductors, energy, finance, metals, telecom
- description: one sentence, max 15 words

Respond with a JSON array only. No prose, no markdown fences.
[{{"name": "...", "ticker": "...", "sanction_date": "...", "sanction_type": "...", "sector": "...", "description": "..."}}, ...]"""


def _validate_event_sync(entry: dict[str, Any]) -> dict[str, Any] | None:
    """Synchronous event validator — called via run_in_executor.

    Accepts an entry if:
      1. yfinance has >= 20 trading days in the 90 days before the claimed event date.
         This confirms the company was publicly listed and trading at that time.
         Deliberately retains delisted/bankrupt stocks: they are often the most extreme
         comparables (e.g. Russian equities 2022) and yfinance keeps their historical data.
      2. The absolute price move over the 10 trading days after the event is >= 1%.
         This filters hallucinated event dates: if no market reaction occurred, the event
         probably didn't happen as described (a real ticker with a fabricated date passes
         check 1 but fails check 2).
    """
    ticker = (entry.get("ticker") or "").strip().lstrip("$")
    sanction_date_str = (entry.get("sanction_date") or "").strip()

    # Pre-filter: reject before hitting yfinance
    if not ticker or not sanction_date_str:
        return None
    if ticker.upper() in _INVALID_TICKERS:
        logger.debug("Rejecting placeholder ticker: %r", ticker)
        return None
    if not _TICKER_RE.match(ticker):
        logger.debug("Rejecting malformed ticker: %r", ticker)
        return None

    # Normalise back onto the entry so downstream uses the clean ticker
    entry = {**entry, "ticker": ticker}

    try:
        sanction_dt = datetime.strptime(sanction_date_str, "%Y-%m-%d")
    except ValueError:
        return None

    start = (sanction_dt - timedelta(days=90)).strftime("%Y-%m-%d")
    end   = (sanction_dt + timedelta(days=20)).strftime("%Y-%m-%d")

    try:
        hist = yf.Ticker(ticker).history(start=start, end=end)
    except Exception:
        logger.debug("yfinance history failed for %s", ticker)
        return None

    if hist is None or len(hist) < 20:
        logger.debug(
            "Insufficient pre-event history for %s (%d rows)",
            ticker, len(hist) if hist is not None else 0,
        )
        return None

    # Check for a measurable post-event price reaction
    try:
        rows: list[tuple[Any, float]] = []
        for idx, row in hist.iterrows():
            close_val = row.get("Close")
            if close_val is None:
                continue
            try:
                price = float(close_val.iloc[0]) if hasattr(close_val, "iloc") else float(close_val)
            except (TypeError, ValueError):
                continue
            rows.append((idx, price))
        rows.sort(key=lambda x: x[0])

        pre  = [(dt, p) for dt, p in rows if dt.date() <= sanction_dt.date()]
        post = [(dt, p) for dt, p in rows if dt.date() >  sanction_dt.date()]

        if not pre or not post:
            return None

        event_price = pre[-1][1]
        post_price  = post[min(9, len(post) - 1)][1]

        if event_price == 0:
            return None
        if abs(post_price - event_price) / event_price * 100 < 1.0:
            logger.debug("No significant market reaction for %s — dropping", ticker)
            return None
    except Exception:
        # Post-event check failed for an unexpected reason; keep the entry since
        # pre-event data was valid (check 1 passed).
        pass

    return entry


async def get_dynamic_comparables(
    sector: str | None,
    sanction_type: str | None,
    country: str | None,
    static_fallback: list[dict[str, Any]],
    sector_groups: dict[str, list[str]] | None = None,
) -> tuple[list[dict[str, Any]], str]:
    """Return (validated_comparable_list, source_label).

    source_label: "cache" | "claude" | "static_fallback"
    sector_groups is passed in to avoid a circular import with sanctions_impact.
    """
    cache_params = dict(
        sector=sector or "",
        sanction_type=sanction_type or "",
        country=country or "",
    )

    # --- Layer 1: cache ---
    cached = get_cached(_CACHE_NS, **cache_params)
    if cached is not None and len(cached) >= 3:
        logger.debug("Comparable sourcer: cache hit (%d entries)", len(cached))
        return cached, "cache"

    # --- Layer 2: Claude suggestions + yfinance historical validation ---
    validated: list[dict[str, Any]] = []
    try:
        client = anthropic.AsyncAnthropic(api_key=config.anthropic_api_key)
        prompt = _SUGGEST_PROMPT.format(
            sector=sector or "general",
            sanction_type=sanction_type or "general",
            country=country or "unknown",
        )
        response = await asyncio.wait_for(
            client.messages.create(
                model=config.model,
                max_tokens=1024,
                messages=[{"role": "user", "content": prompt}],
            ),
            timeout=20.0,
        )
        text = response.content[0].text.strip()

        # Strip markdown fences if present (same pattern as entity_resolver.py)
        if "```" in text:
            start_idx = text.index("```") + 3
            if text[start_idx : start_idx + 4] == "json":
                start_idx += 4
            end_idx = text.rindex("```")
            text = text[start_idx:end_idx].strip()

        candidates: list[dict[str, Any]] = json.loads(text)
        if not isinstance(candidates, list):
            candidates = []

        logger.debug("Comparable sourcer: Claude returned %d candidates", len(candidates))

        # Validate in parallel using a thread pool (yfinance is synchronous)
        loop = asyncio.get_running_loop()
        results = await asyncio.gather(
            *(loop.run_in_executor(None, _validate_event_sync, c) for c in candidates),
            return_exceptions=True,
        )
        for r in results:
            if isinstance(r, dict):
                validated.append(r)

        logger.debug(
            "Comparable sourcer: %d/%d Claude candidates passed validation",
            len(validated), len(candidates),
        )

    except asyncio.TimeoutError:
        logger.warning("Comparable sourcer: Claude call timed out — falling back to static list")
    except Exception as exc:
        logger.warning("Comparable sourcer: Claude call failed (%s) — falling back to static list", exc)

    if len(validated) >= 3:
        set_cached(validated, _CACHE_NS, ttl=_CACHE_TTL, **cache_params)
        return validated, "claude"

    # --- Layer 3: static fallback ---
    logger.debug("Comparable sourcer: using static fallback (%d validated, need >=3)", len(validated))
    comparables = list(static_fallback)

    if sanction_type:
        type_filtered = [c for c in comparables if c.get("sanction_type") == sanction_type]
        if len(type_filtered) >= 3:
            comparables = type_filtered

    if sector and sector_groups:
        related = sector_groups.get(sector.lower(), [sector.lower()])
        sector_filtered = [c for c in comparables if c.get("sector", "").lower() in related]
        if len(sector_filtered) >= 3:
            comparables = sector_filtered

    return comparables, "static_fallback"
