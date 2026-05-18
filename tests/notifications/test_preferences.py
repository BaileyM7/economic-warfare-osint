"""Tests for GET/PUT /api/me/preferences."""

from __future__ import annotations


def test_get_preferences_returns_defaults_when_no_row(app_client, auth_headers):
    resp = app_client.get("/api/me/preferences", headers=auth_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body == {
        "email": None,
        "phone_number": None,
        "sms_enabled": False,
        "email_enabled": False,
        "timezone": "America/New_York",
    }


def test_get_preferences_requires_auth(app_client):
    resp = app_client.get("/api/me/preferences")
    assert resp.status_code == 401


def test_put_preferences_creates_row(app_client, auth_headers):
    payload = {
        "email": "tester@example.com",
        "phone_number": "+12025551234",
        "sms_enabled": True,
        "email_enabled": True,
        "timezone": "America/Los_Angeles",
    }
    resp = app_client.put("/api/me/preferences", json=payload, headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json() == payload

    # Echo via GET
    resp = app_client.get("/api/me/preferences", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json() == payload


def test_put_preferences_updates_existing_row(app_client, auth_headers):
    # Initial save
    app_client.put(
        "/api/me/preferences",
        json={
            "email": "v1@example.com",
            "phone_number": "+12025551111",
            "sms_enabled": True,
            "email_enabled": False,
            "timezone": "America/New_York",
        },
        headers=auth_headers,
    )
    # Update
    updated = {
        "email": "v2@example.com",
        "phone_number": "+12025552222",
        "sms_enabled": False,
        "email_enabled": True,
        "timezone": "Europe/London",
    }
    resp = app_client.put("/api/me/preferences", json=updated, headers=auth_headers)
    assert resp.status_code == 200

    resp = app_client.get("/api/me/preferences", headers=auth_headers)
    assert resp.json() == updated


def test_put_preferences_rejects_invalid_phone(app_client, auth_headers):
    """E.164 violations should 422 from Pydantic validation."""
    resp = app_client.put(
        "/api/me/preferences",
        json={"phone_number": "555-1234"},  # not E.164
        headers=auth_headers,
    )
    assert resp.status_code == 422


def test_put_preferences_accepts_null_phone(app_client, auth_headers):
    """phone_number is optional; null is allowed."""
    resp = app_client.put(
        "/api/me/preferences",
        json={"phone_number": None, "email_enabled": True, "email": "x@x.com"},
        headers=auth_headers,
    )
    assert resp.status_code == 200


def test_put_preferences_requires_auth(app_client):
    resp = app_client.put("/api/me/preferences", json={})
    assert resp.status_code == 401


def test_put_preferences_with_defaults_only(app_client, auth_headers):
    """Empty PUT body uses all default values."""
    resp = app_client.put("/api/me/preferences", json={}, headers=auth_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body == {
        "email": None,
        "phone_number": None,
        "sms_enabled": False,
        "email_enabled": False,
        "timezone": "America/New_York",
    }


def test_put_preferences_per_user_isolation(app_client, auth_headers):
    """A user's PUT should only affect their own row.

    The auth_headers fixture is for username 'tester'. Pre-seed an 'alice' row
    and verify it's untouched by tester's PUT.
    """
    from src.db import get_db

    conn = get_db()
    try:
        conn.execute(
            "INSERT INTO users (username, email, sms_enabled, email_enabled, timezone) "
            "VALUES ('alice', 'alice@example.com', 1, 1, 'Europe/Paris')"
        )
        conn.commit()
    finally:
        conn.close()

    # Tester updates their prefs
    app_client.put(
        "/api/me/preferences",
        json={
            "email": "tester-new@example.com",
            "sms_enabled": True,
            "email_enabled": True,
            "timezone": "America/Chicago",
        },
        headers=auth_headers,
    )

    # Alice's row is unchanged
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT email, sms_enabled, email_enabled, timezone FROM users WHERE username = 'alice'"
        ).fetchone()
    finally:
        conn.close()
    assert row["email"] == "alice@example.com"
    assert row["sms_enabled"] == 1
    assert row["email_enabled"] == 1
    assert row["timezone"] == "Europe/Paris"
