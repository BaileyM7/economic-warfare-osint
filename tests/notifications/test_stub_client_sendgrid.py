"""Tests for the SendGrid stub client used during local dev/testing."""

from __future__ import annotations

import json
import re

import pytest
from sendgrid.helpers.mail import Mail

from src.notifications import stub_client_sendgrid
from src.notifications.stub_client_sendgrid import (
    StubSendGridClient,
    StubSendGridResponse,
    clear_sent_mails,
    sent_mails,
)

MSG_ID_RE = re.compile(r"^sg-stub-[0-9a-f]{16}$")


@pytest.fixture(autouse=True)
def _reset_state(tmp_path, monkeypatch):
    """Each test: empty sent_mails + isolate the JSONL log to a tmpdir."""
    clear_sent_mails()
    log_path = tmp_path / "sendgrid_stub.jsonl"
    monkeypatch.setattr(stub_client_sendgrid, "STUB_LOG_PATH", log_path)
    yield


def _build_mail(
    to_email: str = "alice@example.com",
    subject: str = "Weekly Brief - 3 watchlist updates",
    plain_text: str = "WEEKLY BRIEF...\nplain body content",
    html: str = "<!DOCTYPE html><html><body>html body content</body></html>",
) -> Mail:
    """Construct a real SendGrid Mail object so we exercise its .get() shape."""
    return Mail(
        from_email=("noreply@emissary.demo", "Emissary Weekly Brief"),
        to_emails=to_email,
        subject=subject,
        plain_text_content=plain_text,
        html_content=html,
    )


def test_stub_send_returns_realistic_response():
    """The stub returns a Response-shaped object with status 202 + X-Message-Id."""
    client = StubSendGridClient()
    resp = client.send(_build_mail())

    assert isinstance(resp, StubSendGridResponse)
    assert resp.status_code == 202
    assert "X-Message-Id" in resp.headers
    assert MSG_ID_RE.match(resp.headers["X-Message-Id"]), (
        f"X-Message-Id {resp.headers['X-Message-Id']!r} doesn't match sg-stub-<hex16>"
    )
    assert resp.body == b""


def test_stub_records_in_memory():
    client = StubSendGridClient()
    client.send(_build_mail(subject="first"))
    client.send(_build_mail(subject="second"))

    assert len(sent_mails) == 2
    assert sent_mails[0].subject == "first"
    assert sent_mails[1].subject == "second"


def test_clear_sent_mails_empties_list():
    client = StubSendGridClient()
    client.send(_build_mail())
    assert len(sent_mails) == 1
    clear_sent_mails()
    assert sent_mails == []


def test_stub_writes_jsonl_audit_line():
    client = StubSendGridClient()
    resp = client.send(_build_mail(to_email="bob@example.com", subject="hello world"))

    lines = stub_client_sendgrid.STUB_LOG_PATH.read_text(encoding="utf-8").strip().split("\n")
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert record["message_id"] == resp.headers["X-Message-Id"]
    assert record["to"] == "bob@example.com"
    assert record["subject"] == "hello world"
    assert record["status_code"] == 202
    assert record["plain_text_chars"] > 0
    assert record["html_chars"] > 0
    assert "ts" in record


def test_stub_extracts_to_email_from_personalizations():
    """The recipient lives at mail.get()['personalizations'][0]['to'][0]['email']."""
    client = StubSendGridClient()
    resp = client.send(_build_mail(to_email="charlie@example.com"))
    assert resp.to_email == "charlie@example.com"


def test_stub_extracts_subject_from_mail():
    client = StubSendGridClient()
    resp = client.send(_build_mail(subject="Weekly Brief - 7 watchlist updates"))
    assert resp.subject == "Weekly Brief - 7 watchlist updates"


def test_stub_records_both_plain_text_and_html_excerpts():
    """Both text/plain and text/html content blocks should land in the excerpt."""
    client = StubSendGridClient()
    resp = client.send(
        _build_mail(
            plain_text="PLAIN-TEXT-MARKER-XYZ this is the plain version",
            html="<html><body>HTML-MARKER-XYZ this is the html version</body></html>",
        )
    )
    assert "PLAIN-TEXT-MARKER-XYZ" in resp.plain_text_excerpt
    assert "HTML-MARKER-XYZ" in resp.html_excerpt


def test_stub_handles_mail_without_get_method():
    """If we get a duck-typed object that lacks .get(), don't crash."""
    client = StubSendGridClient()

    class BareMail:
        pass

    resp = client.send(BareMail())
    assert resp.status_code == 202
    assert resp.to_email == ""
    assert resp.subject == ""


# --- Integration with the existing client factory ---


def test_get_sendgrid_client_returns_stub_when_stub_mode_enabled(monkeypatch):
    """The factory swaps to the stub when SENDGRID_STUB_MODE=true."""
    from src.common.config import config as _config
    from src.notifications import clients as clients_mod

    monkeypatch.setattr(_config, "notifications_enabled", True, raising=True)
    monkeypatch.setattr(_config, "sendgrid_stub_mode", True, raising=True)
    monkeypatch.setattr(_config, "sendgrid_api_key", "", raising=True)  # no real creds
    clients_mod.get_sendgrid_client.cache_clear()

    client = clients_mod.get_sendgrid_client()
    assert isinstance(client, StubSendGridClient)

    # Reset cache so other tests aren't poisoned by the stub instance.
    clients_mod.get_sendgrid_client.cache_clear()


def test_get_sendgrid_client_returns_none_when_kill_switch_off(monkeypatch):
    """Kill-switch still applies even if stub mode is on."""
    from src.common.config import config as _config
    from src.notifications import clients as clients_mod

    monkeypatch.setattr(_config, "notifications_enabled", False, raising=True)
    monkeypatch.setattr(_config, "sendgrid_stub_mode", True, raising=True)
    clients_mod.get_sendgrid_client.cache_clear()

    assert clients_mod.get_sendgrid_client() is None

    clients_mod.get_sendgrid_client.cache_clear()


def test_full_email_pipeline_with_stub(monkeypatch, allowlisted_user):
    """End-to-end: send_weekly_digest calls the stub, log row lands in notification_log."""
    from datetime import datetime, timezone

    from src.common.config import config as _config
    from src.notifications import clients as clients_mod
    from src.notifications import email_digest as digest_mod
    from src.notifications.email_digest import WeekData, send_weekly_digest

    monkeypatch.setattr(_config, "notifications_enabled", True, raising=True)
    monkeypatch.setattr(_config, "sendgrid_stub_mode", True, raising=True)
    monkeypatch.setattr(_config, "sendgrid_api_key", "", raising=True)
    clients_mod.get_sendgrid_client.cache_clear()

    # email_digest imported get_sendgrid_client by name at module level --
    # patch digest_mod too so it uses the freshly-cleared factory.
    monkeypatch.setattr(digest_mod, "get_sendgrid_client", clients_mod.get_sendgrid_client)

    wd = WeekData(
        username=allowlisted_user["username"],
        week_iso="2026-W21",
        week_start=datetime(2026, 5, 18, tzinfo=timezone.utc),
        week_end=datetime(2026, 5, 24, tzinfo=timezone.utc),
        top_cards=[
            {
                "severity": "HIGH",
                "entity": "COSCO",
                "synthesis": "OFAC SDN",
                "fetched_at": "2026-05-18T10:00:00Z",
            },
        ],
    )

    result = send_weekly_digest(allowlisted_user, wd)

    assert result.status == "sent"
    assert result.provider_message_id is not None
    assert result.provider_message_id.startswith("sg-stub-")
    # The stub recorded the send
    assert len(sent_mails) == 1
    assert sent_mails[0].to_email == allowlisted_user["email"]
    assert sent_mails[0].subject.startswith("Weekly Brief - ")

    # And the notification_log row landed
    from src.db import get_db

    conn = get_db()
    try:
        row = conn.execute(
            "SELECT status, provider_message_id, digest_week FROM notification_log "
            "WHERE username = ? AND channel = 'email'",
            (allowlisted_user["username"],),
        ).fetchone()
    finally:
        conn.close()
    assert row["status"] == "sent"
    assert row["provider_message_id"].startswith("sg-stub-")
    assert row["digest_week"] == "2026-W21"

    # Reset cache so the stub doesn't leak into subsequent tests.
    clients_mod.get_sendgrid_client.cache_clear()
