"""Per-user Risk Feed watch-list CRUD.

Each row in `watchlist_items` is owned by a single user (keyed by `username`,
which is what `require_auth` returns — see src/auth.py). All endpoints filter
by the calling user's username, so users can only see and modify their own
watch-list items.

Field semantics:
  label        — human-readable name shown in the UI ("Huawei Technologies")
  query        — what the feed builders actually search:
                   * for entity_kind='ticker'             : the ticker symbol ("LMT")
                   * for entity_kind='gdelt_query'        : free-text GDELT query ("Huawei sanctions")
                   * for entity_kind='gdelt_region'       : free-text GDELT query for a region/topic
                   * for entity_kind='sanctions_keyword'  : keyword passed to Trade.gov CSL search
  entity_kind  — one of {'ticker','gdelt_query','gdelt_region','sanctions_keyword'}
  category     — one of {'company_sanctions','people_sanctions','markets'} —
                  drives which Risk Feed column the resulting card lands in.
  active       — soft-delete flag; the refresh routine only loads active=1 rows.
"""

from __future__ import annotations

import logging
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field

from src.common.rate_limit import ENTITY_RESOLVE_LIMIT, limiter

import asyncio
import re

from src.auth import require_auth
from src.db import _new_id, _now, get_db, log_activity
from src.tools.geopolitical.client import gdelt_doc_search
from src.tools.market.client import YFinanceClient
from src.tools.markets.feed import (
    SUGGESTED_ENTITIES,
    SUGGESTED_GDELT_REGIONS,
    SUGGESTED_TICKERS,
)
from src.tools.sanctions.client import OFACClient
from src.tools.screening.client import search_csl

# Maximum number of *active* watch-list rows a user can have at once. Keeps
# fan-out at refresh time bounded so a single user can't blow up the GDELT
# / yfinance budget by adding 200 entities. Adjust as needed.
MAX_ACTIVE_PER_USER = 10

# Loose ticker shape: 1-5 letters, optionally followed by =F (futures) or
# .EXCHANGE (e.g. BHP.AX). Tight enough that "Boeing" doesn't look like a
# ticker, loose enough that LMT, BZ=F, and BHP.AX do.
_TICKER_PATTERN = re.compile(r"^[A-Z]{1,5}(?:=F|\.[A-Z]{1,4})?$")

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/watchlist", tags=["watchlist"])


_VALID_KINDS = {"ticker", "gdelt_query", "gdelt_region", "sanctions_keyword"}
_VALID_CATEGORIES = {"company_sanctions", "people_sanctions", "markets"}


class WatchlistItem(BaseModel):
    id: str
    username: str
    label: str
    query: str
    entity_kind: str
    category: str
    active: bool
    created_at: str
    updated_at: str


class WatchlistCreateRequest(BaseModel):
    label: str = Field(..., min_length=1, max_length=200)
    query: str = Field(..., min_length=1, max_length=500)
    entity_kind: Literal["ticker", "gdelt_query", "gdelt_region", "sanctions_keyword"]
    category: Literal["company_sanctions", "people_sanctions", "markets"]


class WatchlistUpdateRequest(BaseModel):
    label: str | None = Field(default=None, min_length=1, max_length=200)
    query: str | None = Field(default=None, min_length=1, max_length=500)
    active: bool | None = None


def _row_to_item(row) -> WatchlistItem:
    return WatchlistItem(
        id=row["id"],
        username=row["username"],
        label=row["label"],
        query=row["query"],
        entity_kind=row["entity_kind"],
        category=row["category"],
        active=bool(row["active"]),
        created_at=row["created_at"] or "",
        updated_at=row["updated_at"] or "",
    )


@router.get("")
async def list_watchlist(username: str = Depends(require_auth)) -> dict:
    """Return all of the calling user's watch-list items, grouped by category.

    Inactive (soft-deleted) items are included so the UI can offer a "restore"
    affordance later; the refresh routine separately filters to active-only.
    """
    conn = get_db()
    try:
        cur = conn.execute(
            "SELECT * FROM watchlist_items WHERE username = ? ORDER BY category, created_at",
            (username,),
        )
        items = [_row_to_item(r) for r in cur.fetchall()]
    finally:
        conn.close()

    grouped: dict[str, list[WatchlistItem]] = {
        "company_sanctions": [],
        "people_sanctions": [],
        "markets": [],
    }
    for it in items:
        grouped.setdefault(it.category, []).append(it)
    return {"items": items, "grouped": grouped}


@router.post("")
async def create_watchlist_item(
    req: WatchlistCreateRequest,
    username: str = Depends(require_auth),
) -> WatchlistItem:
    """Add a new watch-list item for the calling user.

    Dedupe rule: if an active row already exists for this user with the same
    (entity_kind, query, category), return the existing row instead of
    creating a duplicate. Lets the UI's "+ Watchlist" affordance be safely
    idempotent.

    Caps the active row count at MAX_ACTIVE_PER_USER. Dedupe matches don't
    count against the cap (returning an existing row is a no-op).
    """
    conn = get_db()
    try:
        existing = conn.execute(
            "SELECT * FROM watchlist_items "
            "WHERE username = ? AND entity_kind = ? AND query = ? AND category = ? AND active = 1",
            (username, req.entity_kind, req.query, req.category),
        ).fetchone()
        if existing:
            return _row_to_item(existing)

        active_count = conn.execute(
            "SELECT COUNT(*) FROM watchlist_items WHERE username = ? AND active = 1",
            (username,),
        ).fetchone()[0]
        if active_count >= MAX_ACTIVE_PER_USER:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Watch-list limit reached ({active_count}/{MAX_ACTIVE_PER_USER}). "
                    "Remove an item before adding a new one."
                ),
            )

        item_id = _new_id()
        now = _now()
        conn.execute(
            "INSERT INTO watchlist_items "
            "(id, username, label, query, entity_kind, category, active, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?)",
            (
                item_id,
                username,
                req.label,
                req.query,
                req.entity_kind,
                req.category,
                now,
                now,
            ),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM watchlist_items WHERE id = ?", (item_id,)).fetchone()
    finally:
        conn.close()

    log_activity(
        event_type="watchlist_added",
        message=f"watchlist add ({req.category} / {req.entity_kind}): {req.label}",
        source=username,
        severity="info",
        related_id=item_id,
    )
    return _row_to_item(row)


@router.patch("/{item_id}")
async def update_watchlist_item(
    item_id: str,
    req: WatchlistUpdateRequest,
    username: str = Depends(require_auth),
) -> WatchlistItem:
    """Toggle active or rename label/query for one item.

    `entity_kind` and `category` are intentionally immutable — changing them
    would be equivalent to deleting and re-adding, and would break the
    dedupe key. The UI should use POST to "move" an item to a new category.
    """
    conn = get_db()
    try:
        existing = conn.execute(
            "SELECT * FROM watchlist_items WHERE id = ? AND username = ?",
            (item_id, username),
        ).fetchone()
        if not existing:
            raise HTTPException(status_code=404, detail="watchlist item not found")

        sets: list[str] = []
        params: list = []
        if req.label is not None:
            sets.append("label = ?")
            params.append(req.label)
        if req.query is not None:
            sets.append("query = ?")
            params.append(req.query)
        if req.active is not None:
            # Re-activating a soft-deleted row also counts toward the cap.
            if req.active and not existing["active"]:
                active_count = conn.execute(
                    "SELECT COUNT(*) FROM watchlist_items WHERE username = ? AND active = 1",
                    (username,),
                ).fetchone()[0]
                if active_count >= MAX_ACTIVE_PER_USER:
                    raise HTTPException(
                        status_code=400,
                        detail=(
                            f"Watch-list limit reached ({active_count}/{MAX_ACTIVE_PER_USER}). "
                            "Remove an item before re-activating one."
                        ),
                    )
            sets.append("active = ?")
            params.append(1 if req.active else 0)
        if not sets:
            return _row_to_item(existing)

        sets.append("updated_at = ?")
        params.append(_now())
        params.extend([item_id, username])

        conn.execute(
            f"UPDATE watchlist_items SET {', '.join(sets)} WHERE id = ? AND username = ?",
            params,
        )
        conn.commit()
        row = conn.execute("SELECT * FROM watchlist_items WHERE id = ?", (item_id,)).fetchone()
    finally:
        conn.close()

    log_activity(
        event_type="watchlist_toggled" if req.active is not None else "watchlist_updated",
        message=f"watchlist update: {existing['label']}",
        source=username,
        severity="info",
        related_id=item_id,
    )
    return _row_to_item(row)


@router.delete("/{item_id}")
async def delete_watchlist_item(
    item_id: str,
    username: str = Depends(require_auth),
) -> dict:
    conn = get_db()
    try:
        existing = conn.execute(
            "SELECT * FROM watchlist_items WHERE id = ? AND username = ?",
            (item_id, username),
        ).fetchone()
        if not existing:
            raise HTTPException(status_code=404, detail="watchlist item not found")
        conn.execute(
            "DELETE FROM watchlist_items WHERE id = ? AND username = ?",
            (item_id, username),
        )
        conn.commit()
    finally:
        conn.close()

    log_activity(
        event_type="watchlist_removed",
        message=f"watchlist remove ({existing['category']} / {existing['entity_kind']}): {existing['label']}",
        source=username,
        severity="info",
        related_id=item_id,
    )
    return {"detail": "deleted", "id": item_id}


class ResolveRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)


@router.post("/resolve")
@limiter.limit(ENTITY_RESOLVE_LIMIT)
async def resolve_watchlist_entity(
    request: Request,
    response: Response,
    req: ResolveRequest,
    _username: str = Depends(require_auth),
) -> dict:
    """Take a free-text entity name and propose a single watch-list row the
    user can confirm with one click.

    Strategy (all calls are cheap and cached):
      1. If the name looks like a stock ticker, hit yfinance to confirm.
         A valid profile (longName / shortName / sector) wins — emit a
         markets/ticker suggestion using the company name as the label.
      2. Run OFAC SDN search and Trade.gov CSL search in parallel. A hit
         classifies the entity as company or people sanctions.
      3. Run a small GDELT lookup as a coverage signal. Articles confirm
         the name is real and trackable; tone/title accompany the
         confirmation card so the user can sanity-check.

    Returns one of:
      {resolved: true, suggestion: {label, query, entity_kind, category},
       evidence: [{kind, ...}], confidence: 'high'|'medium'|'low'}
      {resolved: false, suggestion: <markets/gdelt_query fallback>,
       evidence: [...], hint: "couldn't confirm — track anyway?"}
    """
    name = req.name.strip()
    looks_like_ticker = bool(_TICKER_PATTERN.match(name))

    async def _check_ticker() -> dict | None:
        if not looks_like_ticker:
            return None
        try:
            profile = await YFinanceClient().get_stock_profile(name)
        except Exception:
            return None
        long_name = getattr(profile, "name", None)
        sector = getattr(profile, "sector", None)
        # yfinance returns a stub with the ticker as `name` when there's no
        # market data; require at least one structural field to call this a hit.
        if long_name and long_name.upper() != name.upper() or sector:
            return {
                "label": long_name or name,
                "ticker": name.upper(),
                "sector": sector,
                "country": getattr(profile, "country", None),
            }
        return None

    async def _check_ofac() -> tuple[str, str, float] | None:
        """Returns (kind, matched_name, score) for the top SDN hit, or None.

        OFACClient.search filters at score >= 0.3 — that's a *fuzzy* threshold
        ("lockheed martin" can pull up an unrelated SDN entry containing
        "MARTIN"). Caller decides what to do with the score.
        """
        try:
            hits = await OFACClient().search(name)
        except Exception:
            return None
        for hit in (hits or [])[:1]:
            entity_type = getattr(hit, "entity_type", "") or ""
            score = float(getattr(hit, "score", 0.0) or 0.0)
            return (
                "person" if entity_type == "person" else "entity",
                getattr(hit, "name", name),
                score,
            )
        return None

    async def _check_csl() -> str | None:
        """Returns matching list source name (e.g. 'Entity List') if found."""
        try:
            hits = await search_csl(name, limit=2)
        except Exception:
            return None
        for hit in hits or []:
            source = (hit.get("source") or "").strip()
            if source:
                return source
        return None

    async def _check_gdelt() -> list[dict]:
        try:
            events = await gdelt_doc_search(name, days=14, max_records=3)
        except Exception:
            return []
        out: list[dict] = []
        for ev in events[:3]:
            out.append(
                {
                    "title_url": getattr(ev, "source_url", "") or "",
                    "date": getattr(ev, "date", None).isoformat()
                    if getattr(ev, "date", None) is not None
                    else None,
                }
            )
        return out

    ticker_hit, ofac_hit, csl_hit, gdelt_hits = await asyncio.gather(
        _check_ticker(), _check_ofac(), _check_csl(), _check_gdelt()
    )

    # OFAC's fuzzy threshold (0.3) is too loose to drive UX decisions, and
    # even high scores (0.9+) can be misleading because the match algorithm
    # rewards single-token overlap (e.g. "lockheed martin" scores 0.9 against
    # an unrelated SDN entry called "ARTIN"). To call something a strong
    # match, ALL significant tokens from the user's input must literally
    # appear in the matched SDN entity name.
    OFAC_STRONG_MATCH = 0.85

    def _ofac_strong(user_input: str, matched_name: str) -> bool:
        tokens = [t.lower() for t in re.findall(r"[a-z0-9]+", user_input.lower()) if len(t) >= 3]
        if not tokens:
            return False
        name_lower = matched_name.lower()
        return all(t in name_lower for t in tokens)

    evidence: list[dict] = []
    if ticker_hit:
        evidence.append({"kind": "yfinance", **ticker_hit})
    ofac_strong = False
    if ofac_hit:
        kind, matched_name, ofac_score = ofac_hit
        ofac_strong = ofac_score >= OFAC_STRONG_MATCH and _ofac_strong(name, matched_name)
        # Tag the evidence so the UI can show "(partial match)" when score is low,
        # rather than implying the user's input matched cleanly.
        evidence.append(
            {
                "kind": "ofac_sdn",
                "type": kind,
                "name": matched_name,
                "score": round(ofac_score, 2),
                "strong_match": ofac_strong,
            }
        )
    if csl_hit:
        evidence.append({"kind": "csl", "source": csl_hit})
    if gdelt_hits:
        evidence.append({"kind": "gdelt", "articles": gdelt_hits})

    # Tickers are deterministic — yfinance returning a profile with structural
    # fields is a clean override; we use the ticker's long_name as the label.
    if ticker_hit:
        suggestion = {
            "label": ticker_hit["label"],
            "query": ticker_hit["ticker"],
            "entity_kind": "ticker",
            "category": "markets",
        }
        return {
            "resolved": True,
            "suggestion": suggestion,
            "evidence": evidence,
            "confidence": "high",
        }

    # Strong OFAC match → use OFAC's canonical name AND its category. This
    # is the only path where we override the user's typed name, and only
    # when ALL their significant tokens appear in the SDN entity name.
    if ofac_strong:
        kind, matched_name, _score = ofac_hit  # type: ignore[misc]
        suggestion = {
            "label": matched_name,
            "query": matched_name,
            "entity_kind": "gdelt_query",
            "category": "people_sanctions" if kind == "person" else "company_sanctions",
        }
        return {
            "resolved": True,
            "suggestion": suggestion,
            "evidence": evidence,
            "confidence": "high",
        }

    # Weak OFAC match (or no OFAC hit) — keep the user's typed name as
    # canonical. Category falls through to GDELT/CSL signals or a markets
    # default.
    if csl_hit:
        suggestion = {
            "label": name,
            "query": name,
            "entity_kind": "gdelt_query",
            "category": "company_sanctions",
        }
        return {
            "resolved": True,
            "suggestion": suggestion,
            "evidence": evidence,
            "confidence": "medium",
        }

    if gdelt_hits:
        # No sanctions/ticker hit but news coverage exists — track as a news query.
        suggestion = {
            "label": name,
            "query": name,
            "entity_kind": "gdelt_query",
            "category": "markets",
        }
        return {
            "resolved": True,
            "suggestion": suggestion,
            "evidence": evidence,
            "confidence": "low",
        }

    # No signal at all — still let the user track it as a raw GDELT query
    # (maybe coverage will appear later) but flag low confidence.
    return {
        "resolved": False,
        "suggestion": {
            "label": name,
            "query": name,
            "entity_kind": "gdelt_query",
            "category": "markets",
        },
        "evidence": [],
        "hint": (
            "Couldn't confirm this entity from sanctions lists, market data, or recent "
            "news. You can still track it as a news query — the feed will surface "
            "anything GDELT picks up later."
        ),
    }


@router.get("/suggestions")
async def list_watchlist_suggestions(
    _username: str = Depends(require_auth),
) -> dict:
    """Return the bundled "Suggested starter items" the watch-list manager
    renders in its picker tile. Each entry has the exact fields the frontend
    needs to call POST /api/watchlist with one click — no transformation
    required.

    The output is curated to hit exactly **10 suggestions per category**
    (markets / company_sanctions / people_sanctions) so the picker grid
    stays balanced. CSL keyword suggestions are intentionally excluded —
    users who want to track a free-text sanctions keyword can type it into
    the resolver box, which routes through the standard add flow.
    """
    suggestions: list[dict[str, str]] = []

    for ticker, label in SUGGESTED_TICKERS:
        suggestions.append(
            {
                "label": f"{label} ({ticker})",
                "query": ticker,
                "entity_kind": "ticker",
                "category": "markets",
            }
        )

    for query, label in SUGGESTED_GDELT_REGIONS:
        suggestions.append(
            {
                "label": label,
                "query": query,
                "entity_kind": "gdelt_region",
                "category": "markets",
            }
        )

    for query, label, category in SUGGESTED_ENTITIES:
        suggestions.append(
            {
                "label": label,
                "query": query,
                "entity_kind": "gdelt_query",
                "category": category,
            }
        )

    return {"suggestions": suggestions}


# --- Helpers used by the risk_feed router ---


def load_active_items_for_user(username: str) -> dict[str, list[dict]]:
    """Return active watch-list items for *username*, grouped by entity_kind.

    Returned shape:
      {
        "ticker":             [ {"label": "Lockheed", "query": "LMT"}, ... ],
        "gdelt_query":        [ {"label": "Huawei",   "query": "Huawei sanctions", "category": "company_sanctions"}, ... ],
        "gdelt_region":       [ {"label": "...", "query": "..."}, ... ],
        "sanctions_keyword":  [ {"label": "...", "query": "..."}, ... ],
      }

    The risk_feed router consumes this and passes the right slice to each
    feed builder.
    """
    conn = get_db()
    try:
        cur = conn.execute(
            "SELECT label, query, entity_kind, category FROM watchlist_items "
            "WHERE username = ? AND active = 1 ORDER BY entity_kind, created_at",
            (username,),
        )
        rows = cur.fetchall()
    finally:
        conn.close()

    grouped: dict[str, list[dict]] = {
        "ticker": [],
        "gdelt_query": [],
        "gdelt_region": [],
        "sanctions_keyword": [],
    }
    for r in rows:
        grouped.setdefault(r["entity_kind"], []).append(
            {"label": r["label"], "query": r["query"], "category": r["category"]}
        )
    return grouped
