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

_INVALID_TICKERS = frozenset({
    "UNLISTED", "N/A", "NA", "PRIVATE", "UNKNOWN", "TBD", "NONE",
    "DELISTED", "OTC", "OTCPK", "N/L", "-", ".", "NULL", "NIL",
})

_TICKER_RE = re.compile(r'^[A-Za-z0-9.\-\^]{1,15}$')


def _fmt_market_cap(mc: float | None) -> str:
    """Human-readable market cap string for prompt injection."""
    if not mc or mc <= 0:
        return "unknown"
    if mc >= 1e12:
        return f"${mc / 1e12:.1f}T"
    if mc >= 1e9:
        return f"${mc / 1e9:.1f}B"
    if mc >= 1e6:
        return f"${mc / 1e6:.0f}M"
    return f"${mc:,.0f}"


# ---------------------------------------------------------------------------
# Sanctioned comparable sourcing prompt
# ---------------------------------------------------------------------------
_SUGGEST_PROMPT = """\
You are a financial historian specializing in economic sanctions and export controls.

TARGET CONTEXT:
- Sector: "{sector}" (sub-sector: "{sub_sector}")
- Sanction/risk type: "{sanction_type}"
- Target country: "{country}"
- Approximate market cap: {market_cap}
- Severity level: "{severity}"

Find 6-8 real historical cases where a PUBLICLY TRADED company experienced a materially \
similar sanctions or regulatory shock. Match on SEVERITY (blocking vs entity-list vs sectoral), \
MARKET CAP TIER (mega >$200B, large $20-200B, mid $2-20B, small <$2B), and SECTOR.

STRICT RULES:
- Only companies actively listed on a public exchange at the event date.
- No placeholders ("UNLISTED", "N/A", "PRIVATE") — omit if no real ticker exists.
- Ticker = actual exchange symbol (e.g. "BABA", "0763.HK") — no $ prefix.
- Pre-event share price must have been above $2 (no penny stocks).
- No more than 2 cases from the same calendar month — diversify across time periods.
- Each case must have caused a measurable stock price move (>3% within 10 trading days).
- Each ticker may appear only ONCE — pick the most impactful event for that company.
- Prefer US-listed tickers (NYSE/NASDAQ/ADR) with reliable historical data.

For each case provide:
- name: full company name
- ticker: exchange symbol
- sanction_date: YYYY-MM-DD when the shock became public
- sanction_type: one of ofac_ccmc, us_export_control, sectoral, swift_cutoff, retaliation, \
bis_penalty, regulatory_crackdown, delisting_threat
- severity: one of blocking, entity_list, sectoral, delisting_threat, regulatory_crackdown
- market_cap_tier: one of mega, large, mid, small (at time of event)
- sector: one word — tech, semiconductors, energy, finance, metals, telecom
- description: one sentence, max 15 words

Respond with a JSON array only. No prose, no markdown fences.
[{{"name": "...", "ticker": "...", "sanction_date": "...", "sanction_type": "...", \
"severity": "...", "market_cap_tier": "...", "sector": "...", "description": "..."}}]"""


def _validate_event_sync(entry: dict[str, Any]) -> dict[str, Any] | None:
    """Synchronous event validator — called via run_in_executor.

    Rejects entries that fail any of:
      1. Missing/invalid ticker or date
      2. Fewer than 20 trading days in the 90-day pre-event window
      3. Pre-event closing price < $2 (penny stock)
      4. Average pre-event daily volume < 100K shares (illiquid)
      5. Absolute post-event price move < 3% over 10 trading days (non-event)

    Attaches ``_post_event_move_pct`` to passing entries for downstream dedup.
    """
    ticker = (entry.get("ticker") or "").strip().lstrip("$")
    sanction_date_str = (entry.get("sanction_date") or "").strip()

    if not ticker or not sanction_date_str:
        return None
    if ticker.upper() in _INVALID_TICKERS:
        logger.debug("Rejecting placeholder ticker: %r", ticker)
        return None
    if not _TICKER_RE.match(ticker):
        logger.debug("Rejecting malformed ticker: %r", ticker)
        return None

    entry = {**entry, "ticker": ticker}

    try:
        sanction_dt = datetime.strptime(sanction_date_str, "%Y-%m-%d")
    except ValueError:
        return None

    start = (sanction_dt - timedelta(days=90)).strftime("%Y-%m-%d")
    end = (sanction_dt + timedelta(days=20)).strftime("%Y-%m-%d")

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

    try:
        rows: list[tuple[Any, float, float]] = []
        for idx, row in hist.iterrows():
            close_val = row.get("Close")
            vol_val = row.get("Volume")
            if close_val is None:
                continue
            try:
                price = float(close_val.iloc[0]) if hasattr(close_val, "iloc") else float(close_val)
            except (TypeError, ValueError):
                continue
            try:
                volume = float(vol_val.iloc[0]) if hasattr(vol_val, "iloc") else float(vol_val)
            except (TypeError, ValueError, AttributeError):
                volume = 0.0
            rows.append((idx, price, volume))
        rows.sort(key=lambda x: x[0])

        pre = [(dt, p, v) for dt, p, v in rows if dt.date() <= sanction_dt.date()]
        post = [(dt, p, v) for dt, p, v in rows if dt.date() > sanction_dt.date()]

        if not pre or not post:
            return None

        event_price = pre[-1][1]

        if event_price < 2.0:
            logger.debug("Penny stock %s — pre-event close $%.2f", ticker, event_price)
            return None

        pre_volumes = [v for _, _, v in pre if v > 0]
        if pre_volumes:
            avg_vol = sum(pre_volumes) / len(pre_volumes)
            if avg_vol < 100_000:
                logger.debug("Illiquid %s — avg volume %.0f < 100K", ticker, avg_vol)
                return None

        post_price = post[min(9, len(post) - 1)][1]
        if event_price == 0:
            return None

        move_pct = (post_price - event_price) / event_price * 100
        if abs(move_pct) < 3.0:
            logger.debug("Weak reaction for %s (%.1f%%) — dropping", ticker, move_pct)
            return None

        entry["_post_event_move_pct"] = round(move_pct, 2)

    except Exception:
        pass

    return entry


async def get_dynamic_comparables(
    sector: str | None,
    sanction_type: str | None,
    country: str | None,
    static_fallback: list[dict[str, Any]],
    sector_groups: dict[str, list[str]] | None = None,
    *,
    severity: str | None = None,
    market_cap: float | None = None,
    sub_sector: str | None = None,
) -> tuple[list[dict[str, Any]], str]:
    """Return (validated_comparable_list, source_label).

    source_label: "cache" | "claude" | "static_fallback"
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
            sub_sector=sub_sector or sector or "general",
            sanction_type=sanction_type or "general",
            country=country or "unknown",
            market_cap=_fmt_market_cap(market_cap),
            severity=severity or "unknown",
        )
        response = await asyncio.wait_for(
            client.messages.create(
                model=config.model,
                max_tokens=3000,
                messages=[{"role": "user", "content": prompt}],
            ),
            timeout=30.0,
        )
        text = response.content[0].text.strip()

        if "```" in text:
            start_idx = text.index("```") + 3
            if text[start_idx : start_idx + 4] == "json":
                start_idx += 4
            end_idx = text.rindex("```")
            text = text[start_idx:end_idx].strip()

        text = text.strip()
        if text.startswith("[") and not text.endswith("]"):
            last_brace = text.rfind("}")
            if last_brace != -1:
                text = text[: last_brace + 1] + "]"
            else:
                text = "[]"

        candidates: list[dict[str, Any]] = json.loads(text)
        if not isinstance(candidates, list):
            candidates = []

        logger.debug("Comparable sourcer: Claude returned %d candidates", len(candidates))

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

        # Intra-list dedup: if the same ticker appears for multiple event dates,
        # keep only the entry with the largest absolute post-event price move.
        ticker_best: dict[str, dict[str, Any]] = {}
        for v in validated:
            t = (v.get("ticker") or "").upper()
            if not t:
                continue
            move = abs(v.get("_post_event_move_pct", 0))
            existing = ticker_best.get(t)
            if existing is None or move > abs(existing.get("_post_event_move_pct", 0)):
                ticker_best[t] = v
        validated = list(ticker_best.values())

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


# ---------------------------------------------------------------------------
# Non-sanctioned control peer sourcing prompt
# ---------------------------------------------------------------------------
_PEERS_PROMPT = """\
You are building a NON-SANCTIONED CONTROL GROUP for a sanctions impact model.

The purpose: show what a similar company would have done if it had NOT been sanctioned. \
Control peers must be close enough in geography, sector, size, and business model that their \
price behavior is a meaningful counterfactual baseline.

SANCTIONED COMPANY: {company_name} ({ticker})
Sector: {sector}
Industry: {industry}
Approximate market cap: {market_cap}
Sanctions context: {sanctions_context}

STRICT RULES — violating any of these disqualifies a peer:
1. SAME COUNTRY / MARKET first, different sector or sub-vertical second.
   - Chinese company → other Chinese ADRs (HK-listed or NYSE/NASDAQ ADR) \
NOT under the same crackdown/designation. US companies are LAST RESORT.
   - Russian company → other EM banks / energy majors NOT under SWIFT cutoff.
   - Western semiconductor → other chip companies NOT covered by the same BIS rule.
2. NOT subject to the same sanctions, export controls, or crackdowns as {company_name}.
3. Similar market cap — within roughly 0.5x to 2x of the target's market cap.
4. Publicly listed on a major exchange with liquid trading from 2018 onward.
5. Real ticker symbols only — no $ prefix, no "UNLISTED", "N/A", or placeholder text.
6. Do NOT suggest any of these tickers (already used as sanctioned comparables): {excluded_tickers}

EXAMPLES of good vs bad peers:
  Good for Alibaba (Chinese e-commerce): JD (JD.com), TCOM (Trip.com), BEKE (Beike)
  Bad  for Alibaba: AMZN, EBAY  ← different country, different regulatory regime
  Good for Lam Research (US chip equipment): AMAT, KLAC  ← same sector, same geography
  Bad  for Lam Research: ASML  ← if ASML is also under export controls

Select 4-5 peers. Return ONLY a JSON array of ticker strings. No prose, no markdown.
["TICK1", "TICK2", "TICK3", "TICK4"]"""

_PEERS_CACHE_NS = "target_peers"
_PEERS_CACHE_TTL = 7 * 24 * 3600  # 7 days


async def get_target_control_peers(
    ticker: str,
    company_name: str,
    sector: str | None,
    industry: str | None,
    *,
    market_cap: float | None = None,
    excluded_tickers: set[str] | None = None,
    sanctions_context_str: str | None = None,
) -> list[str]:
    """Return non-sanctioned peer tickers similar to the target company.

    ``excluded_tickers`` contains sanctioned comparable tickers that must not
    appear as control peers (cross-list dedup at the prompt level).
    """
    excluded = excluded_tickers or set()

    cache_key = f"{ticker.upper()}|{'_'.join(sorted(excluded))}" if excluded else ticker.upper()
    cache_params = dict(ticker=cache_key)
    cached = get_cached(_PEERS_CACHE_NS, **cache_params)
    if cached is not None:
        logger.debug("Target peers: cache hit for %s (%d peers)", ticker, len(cached))
        return cached  # type: ignore[return-value]

    try:
        client = anthropic.AsyncAnthropic(api_key=config.anthropic_api_key)
        prompt = _PEERS_PROMPT.format(
            company_name=company_name,
            ticker=ticker.upper(),
            sector=sector or "unknown",
            industry=industry or "unknown",
            market_cap=_fmt_market_cap(market_cap),
            sanctions_context=sanctions_context_str or "general sanctions risk",
            excluded_tickers=", ".join(sorted(excluded)) if excluded else "none",
        )
        response = await asyncio.wait_for(
            client.messages.create(
                model=config.model,
                max_tokens=256,
                messages=[{"role": "user", "content": prompt}],
            ),
            timeout=15.0,
        )
        text = response.content[0].text.strip()

        if "```" in text:
            start_idx = text.index("```") + 3
            if text[start_idx : start_idx + 4] == "json":
                start_idx += 4
            end_idx = text.rindex("```")
            text = text[start_idx:end_idx].strip()

        raw: list[str] = json.loads(text)
        if not isinstance(raw, list):
            raw = []

        seen: set[str] = set()
        candidates_clean = []
        for t in raw:
            t = str(t).strip().lstrip("$")
            if t.upper() in _INVALID_TICKERS:
                continue
            if not _TICKER_RE.match(t):
                continue
            if t.upper() == ticker.upper():
                continue
            if t.upper() in seen:
                continue
            if t.upper() in excluded:
                logger.debug("Rejecting peer %s — already a sanctioned comparable", t)
                continue
            seen.add(t.upper())
            candidates_clean.append(t)

        candidates_clean = candidates_clean[:8]

        def _peer_exists(t: str) -> bool:
            try:
                hist = yf.Ticker(t).history(period="1mo")
                return hist is not None and len(hist) >= 5
            except Exception:
                return False

        loop = asyncio.get_running_loop()
        existence = await asyncio.gather(
            *(loop.run_in_executor(None, _peer_exists, t) for t in candidates_clean),
            return_exceptions=True,
        )
        peers = [t for t, ok in zip(candidates_clean, existence) if ok is True]
        peers = peers[:6]

        logger.debug("Target peers for %s: %s (validated %d/%d)", ticker, peers, len(peers), len(candidates_clean))
        set_cached(peers, _PEERS_CACHE_NS, ttl=_PEERS_CACHE_TTL, **cache_params)
        return peers

    except asyncio.TimeoutError:
        logger.warning("Target peers: Claude timed out for %s", ticker)
    except Exception as exc:
        logger.warning("Target peers: failed for %s (%s)", ticker, exc)

    return []
