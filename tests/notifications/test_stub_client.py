"""Tests for the Twilio stub client used during local dev/testing."""

from __future__ import annotations

import json
import re

import pytest
from twilio.base.exceptions import TwilioRestException

from src.notifications import stub_client
from src.notifications.stub_client import (
    StubMessageInstance,
    StubTwilioClient,
    clear_sent_messages,
    sent_messages,
)

SID_RE = re.compile(r"^SM[0-9a-f]{32}$")
ACCT_RE = re.compile(r"^AC[A-Za-z0-9]+$")


@pytest.fixture(autouse=True)
def _reset_state(tmp_path, monkeypatch):
    """Each test: empty sent_messages + isolate the JSONL log to a tmpdir."""
    clear_sent_messages()
    log_path = tmp_path / "twilio_stub.jsonl"
    monkeypatch.setattr(stub_client, "STUB_LOG_PATH", log_path)
    yield


def test_stub_create_returns_realistic_message_instance():
    client = StubTwilioClient()
    msg = client.messages.create(to="+15005550006", from_="+15005550006", body="hi")

    assert isinstance(msg, StubMessageInstance)
    assert SID_RE.match(msg.sid), f"SID {msg.sid!r} doesn't match SM[0-9a-f]{{32}}"
    assert ACCT_RE.match(msg.account_sid)
    assert msg.body == "hi"
    assert msg.from_ == "+15005550006"
    assert msg.to == "+15005550006"
    assert msg.status == "queued"  # matches real Twilio at create-time
    assert msg.direction == "outbound-api"
    assert msg.num_segments == "1"  # STRING not int
    assert msg.num_media == "0"
    assert msg.api_version == "2010-04-01"
    assert msg.date_created is not None
    assert msg.date_sent is None  # populated async by real Twilio
    assert msg.price is None  # populated async
    assert msg.error_code is None
    assert msg.error_message is None
    assert msg.uri.startswith("/2010-04-01/Accounts/")
    assert msg.uri.endswith(f"/Messages/{msg.sid}.json")


def test_stub_records_in_memory():
    client = StubTwilioClient()
    client.messages.create(to="+15005550006", from_="+15005550006", body="first")
    client.messages.create(to="+15005550006", from_="+15005550006", body="second")

    assert len(sent_messages) == 2
    assert sent_messages[0].body == "first"
    assert sent_messages[1].body == "second"


def test_clear_sent_messages_empties_list():
    client = StubTwilioClient()
    client.messages.create(to="+15005550006", from_="+15005550006", body="hi")
    assert len(sent_messages) == 1
    clear_sent_messages()
    assert sent_messages == []


def test_stub_writes_jsonl_audit_line():
    client = StubTwilioClient()
    msg = client.messages.create(to="+15005550006", from_="+15005550006", body="payload-text")

    lines = stub_client.STUB_LOG_PATH.read_text(encoding="utf-8").strip().split("\n")
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert record["sid"] == msg.sid
    assert record["to"] == "+15005550006"
    assert record["from"] == "+15005550006"
    assert record["body"] == "payload-text"
    assert record["status"] == "queued"
    assert "ts" in record


def test_magic_number_invalid_to_raises_21211():
    client = StubTwilioClient()
    with pytest.raises(TwilioRestException) as exc_info:
        client.messages.create(to="+15005550001", from_="+15005550006", body="x")
    err = exc_info.value
    assert err.status == 400
    assert err.code == 21211
    assert "Invalid 'To'" in err.msg or "Invalid 'To'" in str(err.msg)


def test_magic_number_unroutable_raises_21612():
    client = StubTwilioClient()
    with pytest.raises(TwilioRestException) as exc_info:
        client.messages.create(to="+15005550002", from_="+15005550006", body="x")
    assert exc_info.value.code == 21612


def test_magic_number_geo_blocked_raises_21408():
    client = StubTwilioClient()
    with pytest.raises(TwilioRestException) as exc_info:
        client.messages.create(to="+15005550003", from_="+15005550006", body="x")
    assert exc_info.value.code == 21408


def test_magic_number_unsubscribed_raises_21610():
    client = StubTwilioClient()
    with pytest.raises(TwilioRestException) as exc_info:
        client.messages.create(to="+15005550004", from_="+15005550006", body="x")
    assert exc_info.value.code == 21610


def test_magic_number_not_mobile_raises_21614():
    client = StubTwilioClient()
    with pytest.raises(TwilioRestException) as exc_info:
        client.messages.create(to="+15005550009", from_="+15005550006", body="x")
    assert exc_info.value.code == 21614


def test_non_e164_to_raises_21211():
    client = StubTwilioClient()
    with pytest.raises(TwilioRestException) as exc_info:
        client.messages.create(to="555-1234", from_="+15005550006", body="x")
    assert exc_info.value.code == 21211


def test_non_e164_from_raises_21620():
    client = StubTwilioClient()
    with pytest.raises(TwilioRestException) as exc_info:
        client.messages.create(to="+15005550006", from_="not-a-phone", body="x")
    assert exc_info.value.code == 21620


def test_failed_send_not_recorded_in_sent_messages():
    """If create() raises, it should not appear in sent_messages."""
    client = StubTwilioClient()
    with pytest.raises(TwilioRestException):
        client.messages.create(to="+15005550001", from_="+15005550006", body="x")
    assert sent_messages == []


def test_failed_send_not_written_to_jsonl():
    """Failed sends should not show up in the audit log either."""
    client = StubTwilioClient()
    with pytest.raises(TwilioRestException):
        client.messages.create(to="+15005550001", from_="+15005550006", body="x")
    assert not stub_client.STUB_LOG_PATH.exists() or stub_client.STUB_LOG_PATH.read_text() == ""


def test_random_valid_e164_succeeds():
    """Numbers not in the magic list should succeed (don't be stricter than Twilio)."""
    client = StubTwilioClient()
    msg = client.messages.create(to="+12025551234", from_="+15005550006", body="x")
    assert msg.sid.startswith("SM")


# --- Integration with the existing client factory + SMS pipeline ---


def test_get_twilio_client_returns_stub_when_stub_mode_enabled(monkeypatch):
    """The factory swaps to the stub when TWILIO_STUB_MODE=true."""
    from src.common.config import config as _config
    from src.notifications import clients as clients_mod

    monkeypatch.setattr(_config, "notifications_enabled", True, raising=True)
    monkeypatch.setattr(_config, "twilio_stub_mode", True, raising=True)
    monkeypatch.setattr(_config, "twilio_account_sid", "", raising=True)  # no real creds
    clients_mod.get_twilio_client.cache_clear()

    client = clients_mod.get_twilio_client()
    assert isinstance(client, StubTwilioClient)

    # Reset cache so other tests aren't poisoned by the stub instance.
    clients_mod.get_twilio_client.cache_clear()


def test_get_twilio_client_returns_none_when_kill_switch_off(monkeypatch):
    """Kill-switch still applies even if stub mode is on."""
    from src.common.config import config as _config
    from src.notifications import clients as clients_mod

    monkeypatch.setattr(_config, "notifications_enabled", False, raising=True)
    monkeypatch.setattr(_config, "twilio_stub_mode", True, raising=True)
    clients_mod.get_twilio_client.cache_clear()

    assert clients_mod.get_twilio_client() is None

    clients_mod.get_twilio_client.cache_clear()


def test_full_sms_pipeline_with_stub(monkeypatch, allowlisted_user):
    """End-to-end: send_sms_alert calls the stub, log row lands in notification_log."""
    from src.common.config import config as _config
    from src.notifications import clients as clients_mod
    from src.notifications import sms as sms_mod
    from src.notifications.sms import send_sms_alert

    monkeypatch.setattr(_config, "notifications_enabled", True, raising=True)
    monkeypatch.setattr(_config, "twilio_stub_mode", True, raising=True)
    monkeypatch.setattr(_config, "twilio_from_phone", "+15005550006", raising=True)
    clients_mod.get_twilio_client.cache_clear()

    # send_sms_alert imported get_twilio_client by name at module level — patch sms_mod too
    monkeypatch.setattr(sms_mod, "get_twilio_client", clients_mod.get_twilio_client)

    card = {
        "id": "card-stub-1",
        "severity": "HIGH",
        "entity": "ACME",
        "synthesis": "Test event",
        "short_url": "ew.app/r/x",
    }
    result = send_sms_alert(allowlisted_user, card)

    assert result.status == "sent"
    assert SID_RE.match(result.provider_message_id)
    # The stub recorded the send
    assert len(sent_messages) == 1
    assert sent_messages[0].body.startswith("[HIGH] ACME:")

    # And the notification_log row landed
    from src.db import get_db

    conn = get_db()
    try:
        row = conn.execute(
            "SELECT status, provider_message_id FROM notification_log WHERE username = ?",
            (allowlisted_user["username"],),
        ).fetchone()
    finally:
        conn.close()
    assert row["status"] == "sent"
    assert SID_RE.match(row["provider_message_id"])

    # Reset cache so the stub doesn't leak into subsequent tests.
    clients_mod.get_twilio_client.cache_clear()
