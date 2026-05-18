"""Tests for POST /api/notifications/send-weekly-digest."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest


CRON_TOKEN = "test-cron-token-123"


@pytest.fixture
def configured_cron_token(monkeypatch):
    """Pin the cron-token config field for the duration of the test."""
    from src.common.config import config

    monkeypatch.setattr(config, "notifications_cron_token", CRON_TOKEN, raising=True)


def test_send_weekly_digest_rejects_missing_token(app_client, configured_cron_token):
    resp = app_client.post("/api/notifications/send-weekly-digest")
    assert resp.status_code == 403
    assert "invalid cron token" in resp.text.lower()


def test_send_weekly_digest_rejects_wrong_token(app_client, configured_cron_token):
    resp = app_client.post(
        "/api/notifications/send-weekly-digest",
        headers={"X-Cron-Token": "wrong-token"},
    )
    assert resp.status_code == 403


def test_send_weekly_digest_503_when_server_unconfigured(app_client, monkeypatch):
    """If the server has no NOTIFICATIONS_CRON_TOKEN set, return 503 not 200 —
    we never want to silently accept all calls because the env var got dropped."""
    from src.common.config import config

    monkeypatch.setattr(config, "notifications_cron_token", "", raising=True)
    resp = app_client.post(
        "/api/notifications/send-weekly-digest",
        headers={"X-Cron-Token": "anything"},
    )
    assert resp.status_code == 503


def test_send_weekly_digest_zero_users(app_client, configured_cron_token):
    """No opt-in users → 200 with enqueued=0."""
    resp = app_client.post(
        "/api/notifications/send-weekly-digest",
        headers={"X-Cron-Token": CRON_TOKEN},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["enqueued"] == 0
    assert "no opt-in users" in body.get("note", "").lower()


def test_send_weekly_digest_enqueues_eligible_users(app_client, configured_cron_token, monkeypatch):
    """With opt-in users seeded, endpoint enqueues one task per user."""
    from src.db import get_db

    # Seed two users — one eligible, one disabled
    conn = get_db()
    try:
        conn.execute(
            "INSERT INTO users (username, email, email_enabled) VALUES "
            "('alice', 'alice@example.com', 1), "
            "('bob', 'bob@example.com', 0)"  # email_enabled=0 → not eligible
        )
        conn.commit()
    finally:
        conn.close()

    # Spy on the per-user task function so we don't actually call SendGrid
    from src.routers import notifications as notif_router

    invocations = []

    async def fake_send_one(user, anthropic_client):
        invocations.append(user["username"])

    monkeypatch.setattr(notif_router, "_send_one_digest", fake_send_one)

    resp = app_client.post(
        "/api/notifications/send-weekly-digest",
        headers={"X-Cron-Token": CRON_TOKEN},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["enqueued"] == 1

    # The BackgroundTask runs as part of the response cycle inside TestClient
    assert invocations == ["alice"]


def test_send_weekly_digest_skips_users_without_email(
    app_client, configured_cron_token, monkeypatch
):
    """email_enabled=1 but email=NULL → still excluded."""
    from src.db import get_db

    conn = get_db()
    try:
        conn.execute(
            "INSERT INTO users (username, email, email_enabled) VALUES "
            "('alice', NULL, 1), "
            "('bob', '', 1)"
        )
        conn.commit()
    finally:
        conn.close()

    resp = app_client.post(
        "/api/notifications/send-weekly-digest",
        headers={"X-Cron-Token": CRON_TOKEN},
    )
    assert resp.status_code == 200
    assert resp.json()["enqueued"] == 0


def test_per_user_failure_doesnt_break_run(app_client, configured_cron_token, monkeypatch):
    """If the pipeline raises for one user, _send_one_digest swallows it
    and the other user's send still completes.

    We patch build_week_data (called inside _send_one_digest) so the real
    try/except wrapping in _send_one_digest is exercised — this is the
    actual protection we care about for cron runs.
    """
    from src.db import get_db

    conn = get_db()
    try:
        conn.execute(
            "INSERT INTO users (username, email, email_enabled) VALUES "
            "('alice', 'a@x.com', 1), "
            "('bob', 'b@x.com', 1)"
        )
        conn.commit()
    finally:
        conn.close()

    from src.routers import notifications as notif_router

    build_calls: list[str] = []
    send_calls: list[str] = []

    async def flaky_build_week_data(username):
        build_calls.append(username)
        if username == "alice":
            raise RuntimeError("boom")
        # Return a minimal object with the attribute the endpoint sets next.
        return MagicMock(opening_synthesis=None)

    def fake_generate_opening_synthesis(week_data, client):
        return "synthesis"

    def fake_send_weekly_digest(user, week_data):
        send_calls.append(user["username"])

    monkeypatch.setattr(notif_router, "build_week_data", flaky_build_week_data)
    monkeypatch.setattr(notif_router, "generate_opening_synthesis", fake_generate_opening_synthesis)
    monkeypatch.setattr(notif_router, "send_weekly_digest", fake_send_weekly_digest)

    resp = app_client.post(
        "/api/notifications/send-weekly-digest",
        headers={"X-Cron-Token": CRON_TOKEN},
    )
    # Endpoint itself returns 200 — per-user errors are swallowed inside
    # _send_one_digest's try/except.
    assert resp.status_code == 200
    # Both users were attempted (alice raised, bob succeeded).
    assert sorted(build_calls) == ["alice", "bob"]
    # Only bob made it to send_weekly_digest; alice's RuntimeError was caught.
    assert send_calls == ["bob"]
