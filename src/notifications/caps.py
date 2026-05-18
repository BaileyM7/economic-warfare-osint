"""Cap + dedupe helpers backed by the notification_log table."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from src.common.config import config
from src.db import get_db

# Single source of truth for the status enum. notification_log.status is
# free-text in SQLite (no CHECK), so this constants module is the contract.
NOTIFICATION_STATUSES = (
    "sent",
    "failed",
    "skipped_cap",
    "skipped_dedupe",
    "skipped_disabled",
    "skipped_allowlist",
    "skipped_kill_switch",
)


def can_send_sms(username: str) -> bool:
    """True if `username` has sent fewer SMS than the daily cap in the past 24h."""
    cap = config.sms_daily_cap_per_user
    since = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM notification_log "
            "WHERE username = ? AND channel = 'sms' AND status = 'sent' "
            "AND sent_at >= ?",
            (username, since),
        ).fetchone()
        return int(row["n"]) < cap
    finally:
        conn.close()


def already_sent_card(username: str, card_id: str) -> bool:
    """True if a 'sent' SMS for this (username, card_id) already exists."""
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT 1 FROM notification_log "
            "WHERE username = ? AND card_id = ? AND status = 'sent' "
            "LIMIT 1",
            (username, card_id),
        ).fetchone()
        return row is not None
    finally:
        conn.close()


def record_notification(
    username: str,
    channel: str,
    *,
    card_id: str | None = None,
    digest_week: str | None = None,
    provider_message_id: str | None = None,
    status: str,
    error_text: str | None = None,
) -> None:
    """Insert one row into notification_log. All non-`username`/`channel`/`status`
    params are keyword-only because at the call site they're position-confusing
    (which is the card_id and which is the provider message id, etc.).
    """
    if status not in NOTIFICATION_STATUSES:
        # Don't crash callers — log the unknown and persist anyway. This
        # protects against a future status string being introduced upstream
        # without updating this constants module.
        import logging

        logging.getLogger(__name__).warning("unknown notification status: %s", status)
    conn = get_db()
    try:
        conn.execute(
            "INSERT INTO notification_log "
            "(username, channel, card_id, digest_week, provider_message_id, status, error_text) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (username, channel, card_id, digest_week, provider_message_id, status, error_text),
        )
        conn.commit()
    finally:
        conn.close()
