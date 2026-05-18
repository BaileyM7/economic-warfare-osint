"""Weekly email digest: data assembly, template rendering, SendGrid dispatch.

Scope note: build_week_data currently calls the in-memory risk-feed builder
(_build_live_feed_for_user) to get a snapshot of the user's current feed.
There is no persistent 7-day rollup in this codebase; the digest's cadence
is weekly but its content is current-state. A persistent snapshot table
would be a follow-on feature.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape
from sendgrid.helpers.mail import Mail

from src.common.config import config
from src.notifications.caps import record_notification
from src.notifications.clients import get_sendgrid_client, is_user_allowlisted
from src.notifications.scenarios import select_scenario_for_week
from src.notifications.sms import SendResult

log = logging.getLogger(__name__)


@dataclass
class TickerDelta:
    symbol: str
    start: float
    end: float
    pct_change: float


@dataclass
class WeekData:
    """Everything the weekly digest template needs to render for one user."""

    username: str
    week_iso: str  # e.g. "2026-W21"
    week_start: datetime
    week_end: datetime
    top_cards: list[dict] = field(default_factory=list)  # severity-sorted, <=5
    market_deltas: list[TickerDelta] = field(default_factory=list)
    sanctions: list[dict] = field(default_factory=list)  # cards with category=sanctions
    scenario: dict | None = None
    opening_synthesis: str = ""


def _current_iso_week(now: datetime | None = None) -> tuple[str, datetime, datetime]:
    """Return (iso_week_string, start, end) for the week containing `now`."""
    now = now or datetime.now(timezone.utc)
    year, week, _ = now.isocalendar()
    iso = f"{year}-W{week:02d}"
    start_of_week = now - timedelta(days=now.weekday())
    start_of_week = start_of_week.replace(hour=0, minute=0, second=0, microsecond=0)
    end_of_week = start_of_week + timedelta(days=6, hours=23, minutes=59, seconds=59)
    return iso, start_of_week, end_of_week


def _severity_rank(card: dict) -> int:
    """Severity ordering for sorting. Higher number = more urgent."""
    return {"CRITICAL": 4, "HIGH": 3, "MEDIUM": 2, "LOW": 1, "INFO": 0}.get(
        card.get("severity", "INFO"), 0
    )


async def build_week_data(username: str) -> WeekData:
    """Assemble a WeekData snapshot for `username`.

    Calls the live risk-feed builder to get current feed items, then partitions
    them into the digest sections. Does NOT call the LLM — the caller is
    responsible for setting opening_synthesis (see synthesis.py).
    """
    # Avoid circular import at module load time
    from src.routers.risk_feed import _build_live_feed_for_user

    week_iso, week_start, week_end = _current_iso_week()
    items, _errors = await _build_live_feed_for_user(username)

    # Top 5 by severity, then by recency. Use a sort key that's high-to-low.
    sorted_items = sorted(
        items,
        key=lambda c: (_severity_rank(c), c.get("fetched_at", "")),
        reverse=True,
    )
    top_cards = sorted_items[:5]

    # Sanctions roll-up: every sanctions-category item, up to 10
    sanctions = [c for c in items if c.get("category") == "sanctions"][:10]

    # Market deltas: extract from market-category items if they have ticker+pct fields.
    # Real market cards from build_markets_feed have varying shapes; do best-effort
    # extraction and skip cards that don't carry the needed fields.
    market_deltas: list[TickerDelta] = []
    for c in items:
        if c.get("category") != "markets":
            continue
        symbol = c.get("ticker") or c.get("symbol") or c.get("entity")
        start = c.get("price_start") or c.get("week_start_price")
        end = c.get("price_end") or c.get("week_end_price") or c.get("price")
        pct = c.get("pct_change") or c.get("change_pct")
        if symbol and end is not None and pct is not None:
            market_deltas.append(
                TickerDelta(
                    symbol=str(symbol),
                    start=float(start) if start is not None else float(end),
                    end=float(end),
                    pct_change=float(pct),
                )
            )

    scenario = select_scenario_for_week(week_iso)

    return WeekData(
        username=username,
        week_iso=week_iso,
        week_start=week_start,
        week_end=week_end,
        top_cards=top_cards,
        market_deltas=market_deltas[:5],  # cap to 5 to keep email short
        sanctions=sanctions,
        scenario=scenario,
    )


# Lazy Jinja environment so importing this module doesn't touch the filesystem.
_jinja_env: Environment | None = None


def _get_jinja_env() -> Environment:
    global _jinja_env
    if _jinja_env is None:
        _jinja_env = Environment(
            loader=FileSystemLoader(Path(__file__).parent / "templates"),
            autoescape=select_autoescape(["html", "j2"]),
            trim_blocks=True,
            lstrip_blocks=True,
        )
    return _jinja_env


def render_digest(
    week_data: WeekData,
    preferences_url: str = "",
    unsubscribe_url: str = "",
) -> tuple[str, str]:
    """Render the digest to (html, plain_text). Pure function — no I/O."""
    env = _get_jinja_env()
    ctx = {
        "week_data": week_data,
        "preferences_url": preferences_url,
        "unsubscribe_url": unsubscribe_url,
    }
    html = env.get_template("digest.html.j2").render(**ctx)
    text = env.get_template("digest.txt.j2").render(**ctx)
    return html, text


def send_weekly_digest(user: dict, week_data: WeekData) -> SendResult:
    """Send one weekly digest email. Gates mirror sms.send_sms_alert.

    Gates (in order):
      1. allowlist         -> skipped_allowlist     (no log)
      2. email_enabled + email present -> skipped_disabled (no log)
      3. kill-switch/creds -> skipped_kill_switch  (no log)
      4. send              -> sent OR failed        (LOGGED with digest_week)

    No per-week dedupe gate inside this function — Task 7's endpoint should
    check the notification_log if it cares about double-sends for the same week.
    """
    username = user["username"]

    if not is_user_allowlisted(username):
        return SendResult(status="skipped_allowlist")

    if not user.get("email_enabled") or not user.get("email"):
        return SendResult(status="skipped_disabled")

    client = get_sendgrid_client()
    if client is None:
        return SendResult(status="skipped_kill_switch")

    preferences_url = "https://emissary.onrender.com/settings"  # static for demo
    unsubscribe_url = f"https://emissary.onrender.com/unsubscribe?u={username}"
    html, text = render_digest(week_data, preferences_url, unsubscribe_url)

    msg = Mail(
        from_email=(config.newsletter_from_email, config.newsletter_from_name),
        to_emails=user["email"],
        subject=f"Weekly Brief — {len(week_data.top_cards)} watchlist updates",
        plain_text_content=text,
        html_content=html,
    )

    try:
        resp = client.send(msg)
        msg_id = resp.headers.get("X-Message-Id") if hasattr(resp, "headers") else None
        record_notification(
            username,
            "email",
            digest_week=week_data.week_iso,
            provider_message_id=msg_id,
            status="sent",
        )
        return SendResult(status="sent", provider_message_id=msg_id)
    except Exception as e:
        log.exception(
            "sendgrid send failed for username=%s week=%s",
            username,
            week_data.week_iso,
        )
        record_notification(
            username,
            "email",
            digest_week=week_data.week_iso,
            status="failed",
            error_text=str(e),
        )
        return SendResult(status="failed", error=str(e))
