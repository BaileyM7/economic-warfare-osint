"""Tests for the SMS dispatcher that fires after risk-feed refresh."""

from __future__ import annotations

from src.notifications import dispatcher as dispatcher_mod
from src.notifications.dispatcher import dispatch_sms_for_new_cards


# --- Unit tests (no FastAPI client) ---


def test_dispatch_no_user_row_returns_silently(fresh_db, caplog):
    """User isn't in the users table -> no-op, debug log only."""
    cards = [{"id": "c1", "severity": "HIGH", "entity": "X"}]
    dispatch_sms_for_new_cards("unknown_user", cards)
    # No exception. Optionally check debug log.


def test_dispatch_filters_to_high_severity(allowlisted_user, monkeypatch):
    """Only HIGH and CRITICAL cards trigger send_sms_alert."""
    calls = []

    def fake_send(user, card):
        calls.append(card["severity"])
        from src.notifications.sms import SendResult

        return SendResult(status="sent", provider_message_id="SMabc")

    monkeypatch.setattr(dispatcher_mod, "send_sms_alert", fake_send)

    cards = [
        {"id": "c1", "severity": "INFO", "entity": "X"},
        {"id": "c2", "severity": "LOW", "entity": "X"},
        {"id": "c3", "severity": "MEDIUM", "entity": "X"},
        {"id": "c4", "severity": "HIGH", "entity": "X"},
        {"id": "c5", "severity": "CRITICAL", "entity": "X"},
    ]
    dispatch_sms_for_new_cards("alice", cards)
    assert sorted(calls) == ["CRITICAL", "HIGH"]


def test_dispatch_skips_already_sent_cards(allowlisted_user, monkeypatch):
    """already_sent_card returning True prevents the send_sms_alert call."""
    from src.notifications.caps import record_notification

    # Pre-seed one of the cards as already sent
    record_notification("alice", "sms", card_id="c1", provider_message_id="SMold", status="sent")

    calls = []

    def fake_send(user, card):
        calls.append(card["id"])
        from src.notifications.sms import SendResult

        return SendResult(status="sent", provider_message_id="SMnew")

    monkeypatch.setattr(dispatcher_mod, "send_sms_alert", fake_send)

    cards = [
        {"id": "c1", "severity": "HIGH", "entity": "X"},
        {"id": "c2", "severity": "HIGH", "entity": "X"},
    ]
    dispatch_sms_for_new_cards("alice", cards)
    assert calls == ["c2"]


def test_dispatch_skips_card_with_no_id(allowlisted_user, monkeypatch, caplog):
    """Cards without an id field are skipped with a warning, not a crash."""
    calls = []

    def fake_send(user, card):
        calls.append(card)
        from src.notifications.sms import SendResult

        return SendResult(status="sent")

    monkeypatch.setattr(dispatcher_mod, "send_sms_alert", fake_send)

    cards = [{"severity": "HIGH", "entity": "X"}]  # no id
    with caplog.at_level("WARNING"):
        dispatch_sms_for_new_cards("alice", cards)
    assert calls == []
    assert "no id" in caplog.text.lower()


def test_dispatch_swallows_exceptions_from_send_sms_alert(allowlisted_user, monkeypatch):
    """If send_sms_alert raises (programming error), keep going."""

    def fake_send(user, card):
        raise RuntimeError("unexpected programming error")

    monkeypatch.setattr(dispatcher_mod, "send_sms_alert", fake_send)

    cards = [
        {"id": "c1", "severity": "HIGH", "entity": "X"},
        {"id": "c2", "severity": "CRITICAL", "entity": "Y"},
    ]
    # Should not raise
    dispatch_sms_for_new_cards("alice", cards)


# --- Integration test via the FastAPI client ---


def test_refresh_endpoint_schedules_dispatch(app_client, auth_headers, monkeypatch):
    """POST /api/risk-feed/refresh schedules dispatch_sms_for_new_cards as a background task.

    We don't try to verify the SMS itself was sent (BackgroundTasks run AFTER
    the response, inside TestClient's lifespan, but our `notification_log` row
    write is what we actually care about). We spy on the dispatcher to confirm
    it was invoked with the expected args.
    """
    invocations = []

    def fake_dispatcher(username, cards):
        invocations.append((username, [c.get("id") for c in cards]))

    monkeypatch.setattr(
        "src.notifications.dispatcher.dispatch_sms_for_new_cards",
        fake_dispatcher,
    )

    # POST refresh -- this should produce items (fixture mode in tests) and
    # enqueue the dispatcher background task.
    monkeypatch.setenv("RISK_FEED_MODE", "fixture")
    resp = app_client.post("/api/risk-feed/refresh", headers=auth_headers)
    assert resp.status_code == 200

    # BackgroundTasks fire as part of the response cycle; by the time we
    # see the response the task has run inside TestClient.
    assert len(invocations) == 1
    username, card_ids = invocations[0]
    assert username == "tester"
    # Fixture data should produce at least one card
    assert len(card_ids) > 0
