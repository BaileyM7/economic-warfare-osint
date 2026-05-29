"""Tests for POST /api/notifications/twilio/sms-webhook (inbound keywords)."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _enable_stub_mode(monkeypatch):
    """Default to stub mode for every test in this module so existing
    keyword-dispatch tests don't have to compute real X-Twilio-Signature
    headers. Signature-validation tests below override this with their
    own monkeypatch calls to exercise the real validator path.
    """
    from src.common.config import config

    monkeypatch.setattr(config, "twilio_stub_mode", True, raising=True)


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


# --- Signature validation tests ---


def test_webhook_503_when_token_unconfigured(app_client, monkeypatch):
    """No auth token + stub mode off → refuse with 503."""
    from src.common.config import config

    monkeypatch.setattr(config, "twilio_stub_mode", False, raising=True)
    monkeypatch.setattr(config, "twilio_auth_token", "", raising=True)
    resp = _post(app_client, "+12025551234", "STOP")
    assert resp.status_code == 503


def test_webhook_403_when_signature_header_missing(app_client, monkeypatch):
    """Token configured but no X-Twilio-Signature header → 403."""
    from src.common.config import config

    monkeypatch.setattr(config, "twilio_stub_mode", False, raising=True)
    monkeypatch.setattr(config, "twilio_auth_token", "test-auth-token", raising=True)
    resp = _post(app_client, "+12025551234", "STOP")
    assert resp.status_code == 403


def test_webhook_403_when_signature_doesnt_match(app_client, monkeypatch):
    """Wrong signature → 403."""
    from src.common.config import config

    monkeypatch.setattr(config, "twilio_stub_mode", False, raising=True)
    monkeypatch.setattr(config, "twilio_auth_token", "test-auth-token", raising=True)
    resp = app_client.post(
        "/api/notifications/twilio/sms-webhook",
        data={"From": "+12025551234", "Body": "STOP"},
        headers={"X-Twilio-Signature": "obviously-wrong"},
    )
    assert resp.status_code == 403


def test_webhook_200_when_signature_valid(app_client, subscribed_user, monkeypatch):
    """Valid signature computed correctly → request succeeds."""
    from twilio.request_validator import RequestValidator

    from src.common.config import config

    monkeypatch.setattr(config, "twilio_stub_mode", False, raising=True)
    monkeypatch.setattr(config, "twilio_auth_token", "test-auth-token", raising=True)
    monkeypatch.setattr(config, "app_base_url", "https://emissary.test", raising=True)

    # Compute the signature Twilio would send for this exact request.
    validator = RequestValidator("test-auth-token")
    url = "https://emissary.test/api/notifications/twilio/sms-webhook"
    params = {"From": subscribed_user["phone_number"], "Body": "STOP"}
    signature = validator.compute_signature(url, params)

    resp = app_client.post(
        "/api/notifications/twilio/sms-webhook",
        data=params,
        headers={"X-Twilio-Signature": signature},
    )
    assert resp.status_code == 200
    assert "unsubscribed" in resp.text.lower()


def test_webhook_stub_mode_bypasses_signature(app_client, subscribed_user, monkeypatch):
    """Stub mode skips validation entirely — useful for local dev."""
    from src.common.config import config

    monkeypatch.setattr(config, "twilio_stub_mode", True, raising=True)
    # No auth token set, no signature header — should still work.
    monkeypatch.setattr(config, "twilio_auth_token", "", raising=True)
    resp = _post(app_client, subscribed_user["phone_number"], "STOP")
    assert resp.status_code == 200
