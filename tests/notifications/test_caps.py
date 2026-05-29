"""Tests for daily cap + dedupe + log writes."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from src.db import get_db
from src.notifications.caps import (
    already_sent_card,
    can_send_sms,
    record_notification,
)


def _seed_user(username: str = "alice"):
    conn = get_db()
    try:
        conn.execute(
            "INSERT INTO users (username, sms_enabled, email_enabled) VALUES (?, 1, 1)",
            (username,),
        )
        conn.commit()
    finally:
        conn.close()


def test_can_send_sms_true_when_no_history(fresh_db):
    _seed_user("alice")
    assert can_send_sms("alice") is True


def test_can_send_sms_false_when_at_cap(fresh_db, monkeypatch):
    from src.common.config import config as _config

    monkeypatch.setattr(_config, "sms_daily_cap_per_user", 2, raising=True)
    _seed_user("alice")
    record_notification("alice", "sms", card_id="c1", status="sent")
    record_notification("alice", "sms", card_id="c2", status="sent")
    assert can_send_sms("alice") is False


def test_can_send_sms_ignores_skipped_rows(fresh_db, monkeypatch):
    from src.common.config import config as _config

    monkeypatch.setattr(_config, "sms_daily_cap_per_user", 1, raising=True)
    _seed_user("alice")
    # Three skipped rows shouldn't count against the cap
    record_notification("alice", "sms", card_id="c1", status="skipped_cap")
    record_notification("alice", "sms", card_id="c2", status="skipped_dedupe")
    record_notification("alice", "sms", card_id="c3", status="failed")
    assert can_send_sms("alice") is True


def test_can_send_sms_ignores_email_rows(fresh_db, monkeypatch):
    from src.common.config import config as _config

    monkeypatch.setattr(_config, "sms_daily_cap_per_user", 1, raising=True)
    _seed_user("alice")
    # Email sends shouldn't count against the SMS cap
    record_notification("alice", "email", digest_week="2026-W21", status="sent")
    record_notification("alice", "email", digest_week="2026-W22", status="sent")
    assert can_send_sms("alice") is True


def test_can_send_sms_ignores_rows_older_than_24h(fresh_db, monkeypatch):
    from src.common.config import config as _config

    monkeypatch.setattr(_config, "sms_daily_cap_per_user", 1, raising=True)
    _seed_user("alice")
    # Insert a row with sent_at older than 24h
    old = (datetime.now(timezone.utc) - timedelta(hours=25)).isoformat()
    conn = get_db()
    try:
        conn.execute(
            "INSERT INTO notification_log (username, channel, card_id, status, sent_at) "
            "VALUES (?, 'sms', ?, 'sent', ?)",
            ("alice", "c1", old),
        )
        conn.commit()
    finally:
        conn.close()
    assert can_send_sms("alice") is True


def test_already_sent_card_true_after_record(fresh_db):
    _seed_user("alice")
    record_notification("alice", "sms", card_id="c1", status="sent")
    assert already_sent_card("alice", "c1") is True


def test_already_sent_card_false_for_skipped(fresh_db):
    _seed_user("alice")
    # Skipped doesn't count as "already sent" — caller would want to retry
    record_notification("alice", "sms", card_id="c1", status="skipped_cap")
    assert already_sent_card("alice", "c1") is False


def test_already_sent_card_false_for_other_card(fresh_db):
    _seed_user("alice")
    record_notification("alice", "sms", card_id="c1", status="sent")
    assert already_sent_card("alice", "c2") is False


def test_record_notification_unknown_status_warns_but_persists(fresh_db, caplog):
    _seed_user("alice")
    record_notification("alice", "sms", card_id="c1", status="weird_new_status")
    assert "unknown notification status" in caplog.text.lower()
    conn = get_db()
    try:
        row = conn.execute("SELECT status FROM notification_log WHERE username='alice'").fetchone()
    finally:
        conn.close()
    assert row["status"] == "weird_new_status"
