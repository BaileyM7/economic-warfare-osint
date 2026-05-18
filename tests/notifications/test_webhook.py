"""Tests for POST /api/notifications/twilio/sms-webhook (inbound keywords)."""

from __future__ import annotations

import pytest


@pytest.fixture
def subscribed_user(app_client):
    """Seed a user with sms_enabled=1 and a phone number."""
    from src.db import get_db

    conn = get_db()
    try:
        conn.execute(
            "INSERT INTO users (username, phone_number, sms_enabled, email_enabled) "
            "VALUES ('alice', '+12025551234', 1, 1)"
        )
        conn.commit()
    finally:
        conn.close()
    return {"username": "alice", "phone_number": "+12025551234"}


def _post(client, from_number: str, body: str):
    return client.post(
        "/api/notifications/twilio/sms-webhook",
        data={"From": from_number, "Body": body},
    )


def test_stop_keyword_disables_sms(app_client, subscribed_user):
    resp = _post(app_client, subscribed_user["phone_number"], "STOP")
    assert resp.status_code == 200
    assert "<Response>" in resp.text
    assert "unsubscribed" in resp.text.lower()
    # DB state flipped
    from src.db import get_db

    conn = get_db()
    try:
        row = conn.execute(
            "SELECT sms_enabled, unsubscribed_at FROM users WHERE phone_number = ?",
            (subscribed_user["phone_number"],),
        ).fetchone()
    finally:
        conn.close()
    assert row["sms_enabled"] == 0
    assert row["unsubscribed_at"] is not None


def test_stop_with_extra_words_still_works(app_client, subscribed_user):
    """`STOP please` should still trigger the STOP path."""
    resp = _post(app_client, subscribed_user["phone_number"], "STOP please")
    assert resp.status_code == 200
    assert "unsubscribed" in resp.text.lower()


def test_start_keyword_reenables_sms(app_client, subscribed_user):
    # First disable
    _post(app_client, subscribed_user["phone_number"], "STOP")
    # Now re-enable
    resp = _post(app_client, subscribed_user["phone_number"], "START")
    assert resp.status_code == 200
    assert "subscribed" in resp.text.lower()

    from src.db import get_db

    conn = get_db()
    try:
        row = conn.execute(
            "SELECT sms_enabled, unsubscribed_at FROM users WHERE phone_number = ?",
            (subscribed_user["phone_number"],),
        ).fetchone()
    finally:
        conn.close()
    assert row["sms_enabled"] == 1
    assert row["unsubscribed_at"] is None  # cleared on re-subscribe


def test_unstop_keyword_works_same_as_start(app_client, subscribed_user):
    _post(app_client, subscribed_user["phone_number"], "STOP")
    resp = _post(app_client, subscribed_user["phone_number"], "UNSTOP")
    assert resp.status_code == 200
    from src.db import get_db

    conn = get_db()
    try:
        row = conn.execute(
            "SELECT sms_enabled FROM users WHERE phone_number = ?",
            (subscribed_user["phone_number"],),
        ).fetchone()
    finally:
        conn.close()
    assert row["sms_enabled"] == 1


def test_help_keyword_returns_help_text(app_client, subscribed_user):
    resp = _post(app_client, subscribed_user["phone_number"], "HELP")
    assert resp.status_code == 200
    text = resp.text.lower()
    assert "stop" in text  # mentions STOP for opt-out
    assert "/settings" in resp.text  # the management URL


def test_unknown_number_gets_recognition_response(app_client):
    """A POST from a phone we don't have on file → 'not recognized' message."""
    resp = _post(app_client, "+19999999999", "STOP")
    assert resp.status_code == 200
    assert "not recognized" in resp.text.lower()
    # AND no row was created
    from src.db import get_db

    conn = get_db()
    try:
        row = conn.execute(
            "SELECT username FROM users WHERE phone_number = ?",
            ("+19999999999",),
        ).fetchone()
    finally:
        conn.close()
    assert row is None


def test_conversational_message_falls_back_to_help(app_client, subscribed_user):
    """Random text from a known user → friendly help nudge, no state change."""
    resp = _post(app_client, subscribed_user["phone_number"], "thanks for the alert")
    assert resp.status_code == 200
    assert "stop" in resp.text.lower()
    # No state change
    from src.db import get_db

    conn = get_db()
    try:
        row = conn.execute(
            "SELECT sms_enabled, unsubscribed_at FROM users WHERE phone_number = ?",
            (subscribed_user["phone_number"],),
        ).fetchone()
    finally:
        conn.close()
    assert row["sms_enabled"] == 1
    assert row["unsubscribed_at"] is None


def test_case_insensitive_keyword(app_client, subscribed_user):
    """`stop` lowercase should work same as `STOP`."""
    resp = _post(app_client, subscribed_user["phone_number"], "stop")
    assert resp.status_code == 200
    assert "unsubscribed" in resp.text.lower()


def test_returns_xml_content_type(app_client, subscribed_user):
    resp = _post(app_client, subscribed_user["phone_number"], "HELP")
    assert resp.headers["content-type"].startswith("application/xml")


def test_empty_body_doesnt_crash(app_client, subscribed_user):
    """Pathological: empty Body shouldn't 500."""
    resp = _post(app_client, subscribed_user["phone_number"], "")
    assert resp.status_code == 200
    # Empty body falls through to "no keyword match" → help text
    assert "stop" in resp.text.lower()
