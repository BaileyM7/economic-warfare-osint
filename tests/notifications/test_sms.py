"""Tests for SMS composition + send dispatch."""

from __future__ import annotations

from unittest.mock import MagicMock

from src.notifications import clients as clients_mod
from src.notifications.sms import format_sms_body, send_sms_alert


# --- format_sms_body (pure) ---


def test_format_sms_body_basic(sample_card):
    body = format_sms_body(sample_card)
    assert body.startswith("[HIGH] COSCO:")
    assert "ew.app/r/x9k" in body
    assert "More: " in body
    assert len(body) <= 160
    assert body.isascii()


def test_format_sms_body_truncates_long_synthesis():
    card = {
        "severity": "CRITICAL",
        "entity": "X",
        "synthesis": "Y" * 500,
        "short_url": "ew.app/r/abc",
    }
    body = format_sms_body(card)
    assert len(body) <= 160
    # Truncation marker should be in there (ellipsis before the suffix)
    assert "..." in body
    # The suffix must survive the truncation
    assert body.endswith("More: ew.app/r/abc")


def test_format_sms_body_no_url():
    card = {"severity": "LOW", "entity": "TestCorp", "synthesis": "Nothing to see", "short_url": ""}
    body = format_sms_body(card)
    assert body == "[LOW] TestCorp: Nothing to see"
    assert "More:" not in body


def test_format_sms_body_handles_missing_fields():
    body = format_sms_body({})  # totally empty card
    # Should not crash, should return something stable
    assert body.startswith("[INFO] : ")
    assert len(body) <= 160


# --- send_sms_alert (gated) ---


def test_send_sms_alert_skipped_when_not_allowlisted(non_allowlisted_user, sample_card):
    result = send_sms_alert(non_allowlisted_user, sample_card)
    assert result.status == "skipped_allowlist"
    assert result.provider_message_id is None


def test_send_sms_alert_skipped_when_sms_disabled(allowlisted_user, sample_card):
    allowlisted_user["sms_enabled"] = 0
    result = send_sms_alert(allowlisted_user, sample_card)
    assert result.status == "skipped_disabled"


def test_send_sms_alert_skipped_when_no_phone(allowlisted_user, sample_card):
    allowlisted_user["phone_number"] = None
    result = send_sms_alert(allowlisted_user, sample_card)
    assert result.status == "skipped_disabled"


def test_send_sms_alert_skipped_kill_switch(allowlisted_user, sample_card, monkeypatch):
    # Allowlist + opt-in OK + under cap, but Twilio client is None (default in tests)
    # get_twilio_client returns None because notifications_enabled is False, so
    # we don't need to monkeypatch anything — just call send_sms_alert.
    # The lru_cache on get_twilio_client means we may need to clear it if a
    # previous test populated it; safer to clear explicitly.
    clients_mod.get_twilio_client.cache_clear()
    result = send_sms_alert(allowlisted_user, sample_card)
    assert result.status == "skipped_kill_switch"


def test_send_sms_alert_sends_and_logs(allowlisted_user, sample_card, monkeypatch):
    """Happy path: mock the Twilio client, assert SMS body + log row."""
    fake_message = MagicMock()
    fake_message.sid = "SMabc1234567890"

    fake_client = MagicMock()
    fake_client.messages.create.return_value = fake_message

    # Replace the factory's return value, not the SDK module. Clear cache first.
    clients_mod.get_twilio_client.cache_clear()
    monkeypatch.setattr(clients_mod, "get_twilio_client", lambda: fake_client)
    # Also need to patch the symbol where send_sms_alert imported it
    from src.notifications import sms as sms_mod

    monkeypatch.setattr(sms_mod, "get_twilio_client", lambda: fake_client)

    result = send_sms_alert(allowlisted_user, sample_card)

    assert result.status == "sent"
    assert result.provider_message_id == "SMabc1234567890"

    # Verify Twilio was called with the right shape
    fake_client.messages.create.assert_called_once()
    call_kwargs = fake_client.messages.create.call_args.kwargs
    assert call_kwargs["to"] == "+15005550006"
    assert call_kwargs["body"].startswith("[HIGH] COSCO:")
    assert "ew.app/r/x9k" in call_kwargs["body"]

    # Verify the log row landed
    from src.db import get_db

    conn = get_db()
    try:
        row = conn.execute(
            "SELECT username, channel, card_id, provider_message_id, status "
            "FROM notification_log WHERE username = ?",
            ("alice",),
        ).fetchone()
    finally:
        conn.close()
    assert row["status"] == "sent"
    assert row["card_id"] == sample_card["id"]
    assert row["provider_message_id"] == "SMabc1234567890"


def test_send_sms_alert_failure_logs_error(allowlisted_user, sample_card, monkeypatch):
    fake_client = MagicMock()
    fake_client.messages.create.side_effect = RuntimeError("twilio: 503 service unavailable")

    from src.notifications import sms as sms_mod

    monkeypatch.setattr(sms_mod, "get_twilio_client", lambda: fake_client)

    result = send_sms_alert(allowlisted_user, sample_card)
    assert result.status == "failed"
    assert "503" in result.error

    from src.db import get_db

    conn = get_db()
    try:
        row = conn.execute(
            "SELECT status, error_text FROM notification_log WHERE username = ?",
            ("alice",),
        ).fetchone()
    finally:
        conn.close()
    assert row["status"] == "failed"
    assert "503" in row["error_text"]
