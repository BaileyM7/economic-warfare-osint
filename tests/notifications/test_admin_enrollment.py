"""Tests for admin notification-enrollment endpoints.

Covers GET/POST /api/admin/enrollments and DELETE
/api/admin/enrollments/{username}. End-users have no self-service path
into the `users` table — admins enroll on their behalf.
"""

from __future__ import annotations

import pytest


# --- Helper: admin auth fixture -------------------------------------------


@pytest.fixture
def admin_headers(app_client, monkeypatch) -> dict[str, str]:
    """Promote 'tester' to admin and return bearer headers for them.

    src.auth.is_admin() reads EMISSARY_ADMIN_USERS via os.environ on every
    call (no caching), so monkeypatch.setenv before login is sufficient.
    The login endpoint accepts admin creds via check_admin_credentials,
    so the same tester:testpass works for both the demo-user path and the
    admin path once the env var is set.
    """
    monkeypatch.setenv("EMISSARY_ADMIN_USERS", "tester:testpass")
    resp = app_client.post(
        "/api/auth/login",
        json={"username": "tester", "password": "testpass"},
    )
    resp.raise_for_status()
    token = resp.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


# --- Authorization tests --------------------------------------------------


def test_list_enrollments_requires_auth(app_client):
    resp = app_client.get("/api/admin/enrollments")
    assert resp.status_code == 401


def test_list_enrollments_non_admin_forbidden(app_client, auth_headers):
    """`auth_headers` is for the non-admin 'tester' user."""
    resp = app_client.get("/api/admin/enrollments", headers=auth_headers)
    assert resp.status_code == 403


def test_enroll_user_requires_auth(app_client):
    resp = app_client.post("/api/admin/enrollments", json={"username": "alice"})
    assert resp.status_code == 401


def test_enroll_user_non_admin_forbidden(app_client, auth_headers):
    resp = app_client.post(
        "/api/admin/enrollments",
        json={"username": "alice"},
        headers=auth_headers,
    )
    assert resp.status_code == 403


def test_unenroll_requires_auth(app_client):
    resp = app_client.delete("/api/admin/enrollments/alice")
    assert resp.status_code == 401


def test_unenroll_non_admin_forbidden(app_client, auth_headers):
    resp = app_client.delete("/api/admin/enrollments/alice", headers=auth_headers)
    assert resp.status_code == 403


# --- Happy-path tests (admin) ---------------------------------------------


def test_list_enrollments_empty(app_client, admin_headers):
    resp = app_client.get("/api/admin/enrollments", headers=admin_headers)
    assert resp.status_code == 200
    assert resp.json() == []


def test_enroll_creates_row(app_client, admin_headers):
    payload = {
        "username": "alice",
        "email": "alice@example.com",
        "phone_number": "+12025551234",
        "sms_enabled": True,
        "email_enabled": True,
        "timezone": "America/Los_Angeles",
    }
    resp = app_client.post("/api/admin/enrollments", json=payload, headers=admin_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["username"] == "alice"
    assert body["email"] == "alice@example.com"
    assert body["phone_number"] == "+12025551234"
    assert body["sms_enabled"] is True
    assert body["email_enabled"] is True
    assert body["timezone"] == "America/Los_Angeles"
    assert body["created_at"] is not None


def test_enroll_upserts(app_client, admin_headers):
    # Initial enrollment
    app_client.post(
        "/api/admin/enrollments",
        json={
            "username": "alice",
            "phone_number": "+12025551111",
            "sms_enabled": True,
            "email_enabled": False,
            "timezone": "America/New_York",
        },
        headers=admin_headers,
    )
    # Update with different phone
    resp = app_client.post(
        "/api/admin/enrollments",
        json={
            "username": "alice",
            "phone_number": "+12025552222",
            "sms_enabled": False,
            "email_enabled": True,
            "timezone": "Europe/London",
        },
        headers=admin_headers,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["phone_number"] == "+12025552222"
    assert body["sms_enabled"] is False
    assert body["email_enabled"] is True
    assert body["timezone"] == "Europe/London"

    # List should show one row, with updated values
    resp = app_client.get("/api/admin/enrollments", headers=admin_headers)
    rows = resp.json()
    assert len(rows) == 1
    assert rows[0]["phone_number"] == "+12025552222"


def test_enroll_invalid_phone_format(app_client, admin_headers):
    resp = app_client.post(
        "/api/admin/enrollments",
        json={"username": "alice", "phone_number": "555-1234"},  # not E.164
        headers=admin_headers,
    )
    assert resp.status_code == 422


def test_enroll_accepts_null_phone(app_client, admin_headers):
    resp = app_client.post(
        "/api/admin/enrollments",
        json={
            "username": "alice",
            "phone_number": None,
            "email": "alice@x.com",
            "email_enabled": True,
        },
        headers=admin_headers,
    )
    assert resp.status_code == 200


def test_list_returns_multiple_enrollments(app_client, admin_headers):
    for u in ("alice", "bob", "carol"):
        app_client.post(
            "/api/admin/enrollments",
            json={"username": u, "email": f"{u}@x.com", "email_enabled": True},
            headers=admin_headers,
        )
    resp = app_client.get("/api/admin/enrollments", headers=admin_headers)
    rows = resp.json()
    assert len(rows) == 3
    assert sorted(r["username"] for r in rows) == ["alice", "bob", "carol"]


def test_unenroll_removes_row(app_client, admin_headers):
    app_client.post(
        "/api/admin/enrollments",
        json={"username": "alice", "email": "alice@x.com", "email_enabled": True},
        headers=admin_headers,
    )
    resp = app_client.delete("/api/admin/enrollments/alice", headers=admin_headers)
    assert resp.status_code == 200
    assert resp.json() == {"username": "alice", "deleted": True}
    # Subsequent GET excludes them
    resp = app_client.get("/api/admin/enrollments", headers=admin_headers)
    assert resp.json() == []


def test_unenroll_404_when_missing(app_client, admin_headers):
    resp = app_client.delete("/api/admin/enrollments/nobody", headers=admin_headers)
    assert resp.status_code == 404


def test_enroll_writes_audit_log(app_client, admin_headers):
    """log_activity should record the enrollment with admin=source."""
    app_client.post(
        "/api/admin/enrollments",
        json={"username": "alice", "email": "alice@x.com", "email_enabled": True},
        headers=admin_headers,
    )
    # Inspect activity_log table for the matching row
    from src.db import get_db

    conn = get_db()
    try:
        row = conn.execute(
            "SELECT event_type, message, source, related_id FROM activity_log "
            "WHERE event_type = 'notifications_enrollment' "
            "ORDER BY id DESC LIMIT 1"
        ).fetchone()
    finally:
        conn.close()
    assert row is not None
    assert row["source"] == "tester"  # the admin (per the fixture)
    assert row["related_id"] == "alice"
    assert "enrolled alice" in row["message"]


def test_unenroll_writes_audit_log(app_client, admin_headers):
    app_client.post(
        "/api/admin/enrollments",
        json={"username": "alice", "email_enabled": True},
        headers=admin_headers,
    )
    app_client.delete("/api/admin/enrollments/alice", headers=admin_headers)
    from src.db import get_db

    conn = get_db()
    try:
        row = conn.execute(
            "SELECT event_type, message, source, related_id FROM activity_log "
            "WHERE event_type = 'notifications_unenrollment' "
            "ORDER BY id DESC LIMIT 1"
        ).fetchone()
    finally:
        conn.close()
    assert row is not None
    assert row["source"] == "tester"
    assert row["related_id"] == "alice"


# --- Test-send endpoints --------------------------------------------------


def _enroll_alice_for_sms(app_client, admin_headers):
    """Helper: enroll alice with phone + sms_enabled=1 + email + email_enabled=1."""
    app_client.post(
        "/api/admin/enrollments",
        json={
            "username": "alice",
            "email": "alice@example.com",
            "phone_number": "+15005550006",
            "sms_enabled": True,
            "email_enabled": True,
            "timezone": "America/New_York",
        },
        headers=admin_headers,
    )


# Auth + 404 + 400 cases


def test_test_sms_requires_admin(app_client, auth_headers):
    resp = app_client.post("/api/admin/enrollments/alice/test-sms", headers=auth_headers)
    assert resp.status_code == 403


def test_test_sms_401_when_no_auth(app_client):
    resp = app_client.post("/api/admin/enrollments/alice/test-sms")
    assert resp.status_code == 401


def test_test_sms_404_when_user_missing(app_client, admin_headers):
    resp = app_client.post("/api/admin/enrollments/nobody/test-sms", headers=admin_headers)
    assert resp.status_code == 404


def test_test_sms_400_when_phone_missing(app_client, admin_headers):
    # Enroll alice with email only, no phone
    app_client.post(
        "/api/admin/enrollments",
        json={"username": "alice", "email": "a@x.com", "email_enabled": True},
        headers=admin_headers,
    )
    resp = app_client.post("/api/admin/enrollments/alice/test-sms", headers=admin_headers)
    assert resp.status_code == 400


def test_test_email_requires_admin(app_client, auth_headers):
    resp = app_client.post("/api/admin/enrollments/alice/test-email", headers=auth_headers)
    assert resp.status_code == 403


def test_test_email_404_when_user_missing(app_client, admin_headers):
    resp = app_client.post("/api/admin/enrollments/nobody/test-email", headers=admin_headers)
    assert resp.status_code == 404


def test_test_email_400_when_email_missing(app_client, admin_headers):
    app_client.post(
        "/api/admin/enrollments",
        json={"username": "alice", "phone_number": "+15005550006", "sms_enabled": True},
        headers=admin_headers,
    )
    resp = app_client.post("/api/admin/enrollments/alice/test-email", headers=admin_headers)
    assert resp.status_code == 400


# Happy paths with mocked providers


def test_test_sms_happy_path(app_client, admin_headers, monkeypatch):
    _enroll_alice_for_sms(app_client, admin_headers)

    # Mock the Twilio client at the call-site (sms_mod.get_twilio_client)
    from unittest.mock import MagicMock

    from src.common.config import config as _config
    from src.notifications import clients as clients_mod
    from src.notifications import sms as sms_mod

    fake_msg = MagicMock()
    fake_msg.sid = "SMtestbutton123"
    fake_client = MagicMock()
    fake_client.messages.create.return_value = fake_msg

    # Also need NOTIFICATIONS_ALLOWLIST to include alice
    monkeypatch.setattr(_config, "notifications_allowlist", "alice", raising=True)
    clients_mod.get_twilio_client.cache_clear()
    monkeypatch.setattr(sms_mod, "get_twilio_client", lambda: fake_client)

    resp = app_client.post("/api/admin/enrollments/alice/test-sms", headers=admin_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "sent"
    assert body["provider_message_id"] == "SMtestbutton123"

    # Twilio was called with the test body
    fake_client.messages.create.assert_called_once()
    call_kwargs = fake_client.messages.create.call_args.kwargs
    assert "[HIGH] TEST:" in call_kwargs["body"]

    # log_activity row was written
    from src.db import get_db

    conn = get_db()
    try:
        row = conn.execute(
            "SELECT event_type, source, related_id FROM activity_log "
            "WHERE event_type = 'notifications_test_sms' ORDER BY id DESC LIMIT 1"
        ).fetchone()
    finally:
        conn.close()
    assert row is not None
    assert row["source"] == "tester"  # admin per the fixture
    assert row["related_id"] == "alice"


def test_test_sms_bypasses_daily_cap(app_client, admin_headers, monkeypatch):
    """Even if user is at the cap, test sends should go through."""
    _enroll_alice_for_sms(app_client, admin_headers)

    from unittest.mock import MagicMock

    from src.common.config import config as _config
    from src.notifications import clients as clients_mod
    from src.notifications import sms as sms_mod
    from src.notifications.caps import record_notification

    monkeypatch.setattr(_config, "notifications_allowlist", "alice", raising=True)
    monkeypatch.setattr(_config, "sms_daily_cap_per_user", 1, raising=True)

    fake_msg = MagicMock()
    fake_msg.sid = "SMtest"
    fake_client = MagicMock()
    fake_client.messages.create.return_value = fake_msg
    clients_mod.get_twilio_client.cache_clear()
    monkeypatch.setattr(sms_mod, "get_twilio_client", lambda: fake_client)

    # Seed alice at the cap: 1 prior sent row in last 24h
    record_notification(
        "alice", "sms", card_id="prior", provider_message_id="SMprior", status="sent"
    )

    # Real sends would now hit skipped_cap. Test send should bypass.
    resp = app_client.post("/api/admin/enrollments/alice/test-sms", headers=admin_headers)
    assert resp.status_code == 200
    assert resp.json()["status"] == "sent"


def test_test_email_happy_path(app_client, admin_headers, monkeypatch):
    _enroll_alice_for_sms(app_client, admin_headers)

    from unittest.mock import MagicMock

    from src.common.config import config as _config
    from src.notifications import clients as clients_mod
    from src.notifications import email_digest as digest_mod

    monkeypatch.setattr(_config, "notifications_allowlist", "alice", raising=True)

    fake_resp = MagicMock()
    fake_resp.headers = {"X-Message-Id": "sg-test-msg-id"}
    fake_client = MagicMock()
    fake_client.send.return_value = fake_resp
    clients_mod.get_sendgrid_client.cache_clear()
    monkeypatch.setattr(digest_mod, "get_sendgrid_client", lambda: fake_client)

    resp = app_client.post("/api/admin/enrollments/alice/test-email", headers=admin_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "sent"
    assert body["provider_message_id"] == "sg-test-msg-id"

    fake_client.send.assert_called_once()
    # Confirm fallback synthesis used (no LLM): the opening should be the
    # FALLBACK_TEMPLATE formatted, mentioning "watchlist saw N updates"
    mail = fake_client.send.call_args.args[0]
    mail_dict = mail.get()
    plain = next((c["value"] for c in mail_dict["content"] if c["type"] == "text/plain"), "")
    assert "watchlist" in plain.lower()
    assert "[SAMPLE]" in plain  # the test card synthesis text marker

    # log_activity row was written
    from src.db import get_db

    conn = get_db()
    try:
        row = conn.execute(
            "SELECT event_type, source, related_id FROM activity_log "
            "WHERE event_type = 'notifications_test_email' ORDER BY id DESC LIMIT 1"
        ).fetchone()
    finally:
        conn.close()
    assert row is not None
    assert row["source"] == "tester"
    assert row["related_id"] == "alice"
