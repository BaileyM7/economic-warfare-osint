"""Dispatch SMS alerts to opt-in users after a risk-feed refresh.

Called as a FastAPI BackgroundTask from `/api/risk-feed/refresh` so that
sends happen out-of-band and never delay the API response. Each card is
filtered by severity, deduplicated via notification_log, then handed to
send_sms_alert which handles the per-user gates (allowlist, opt-in, cap,
kill-switch) and writes the log row.
"""

from __future__ import annotations

import logging

from src.db import get_db
from src.notifications.caps import already_sent_card
from src.notifications.sms import send_sms_alert

log = logging.getLogger(__name__)

# Severity values that trigger SMS. Mirrors the design decision from
# brainstorming (event-driven SMS for HIGH+ watchlist matches).
SMS_TRIGGER_SEVERITIES = frozenset({"HIGH", "CRITICAL"})


def _load_user(username: str) -> dict | None:
    """Look up the users row for `username`. Returns None if not registered."""
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT username, email, phone_number, sms_enabled, email_enabled "
            "FROM users WHERE username = ?",
            (username,),
        ).fetchone()
    finally:
        conn.close()
    return dict(row) if row else None


def dispatch_sms_for_new_cards(username: str, cards: list[dict]) -> None:
    """For each HIGH+ card not already sent to this user, fire an SMS.

    Safe to call when the user has no users-row, no opt-in, or no cards —
    all paths short-circuit cleanly. Never raises; failures are logged.
    """
    user = _load_user(username)
    if user is None:
        log.debug("dispatch_sms: no users row for %s, nothing to send", username)
        return

    sent = 0
    skipped = 0
    for card in cards:
        if card.get("severity") not in SMS_TRIGGER_SEVERITIES:
            continue
        card_id = card.get("id")
        if not card_id:
            log.warning(
                "dispatch_sms: card has no id, skipping (entity=%s)",
                card.get("entity"),
            )
            continue
        if already_sent_card(username, card_id):
            skipped += 1
            continue
        try:
            result = send_sms_alert(user, card)
            if result.status == "sent":
                sent += 1
            else:
                skipped += 1
        except Exception:
            # send_sms_alert is itself broad-except, but defend against
            # programming errors that bubble through (e.g., bad card shape).
            log.exception("dispatch_sms: unexpected error sending card_id=%s", card_id)
            skipped += 1

    if sent or skipped:
        log.info("dispatch_sms for %s: sent=%d skipped=%d", username, sent, skipped)
