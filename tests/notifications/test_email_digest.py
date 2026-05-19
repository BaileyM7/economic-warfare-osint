"""Tests for the weekly email digest: data assembly, template render, send dispatch."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from src.notifications import email_digest as digest_mod
from src.notifications.email_digest import (
    TickerDelta,
    WeekData,
    _current_iso_week,
    _severity_rank,
    build_week_data,
    render_digest,
    send_weekly_digest,
)


# --- Pure helpers ---


def test_current_iso_week_format():
    iso, start, end = _current_iso_week(datetime(2026, 5, 18, tzinfo=timezone.utc))
    assert iso == "2026-W21"
    assert start.weekday() == 0  # Monday
    assert (end - start).days == 6


def test_severity_rank_orders_correctly():
    assert _severity_rank({"severity": "CRITICAL"}) > _severity_rank({"severity": "HIGH"})
    assert _severity_rank({"severity": "HIGH"}) > _severity_rank({"severity": "LOW"})
    assert _severity_rank({"severity": "UNKNOWN"}) == 0
    assert _severity_rank({}) == 0


# --- build_week_data ---


@pytest.mark.asyncio
async def test_build_week_data_partitions_items(monkeypatch):
    fake_items = [
        {
            "severity": "HIGH",
            "entity": "COSCO",
            "category": "sanctions",
            "synthesis": "OFAC SDN",
            "fetched_at": "2026-05-18T10:00:00Z",
        },
        {
            "severity": "MEDIUM",
            "entity": "Sinopec",
            "category": "markets",
            "ticker": "SNP",
            "price_start": 50.0,
            "price_end": 52.0,
            "pct_change": 4.0,
            "fetched_at": "2026-05-17T10:00:00Z",
        },
        {
            "severity": "LOW",
            "entity": "Foo",
            "category": "other",
            "fetched_at": "2026-05-16T10:00:00Z",
        },
    ]

    async def fake_builder(username):
        return fake_items, []

    monkeypatch.setattr("src.routers.risk_feed._build_live_feed_for_user", fake_builder)

    wd = await build_week_data("alice")
    assert wd.username == "alice"
    assert wd.week_iso.startswith("20")
    assert len(wd.top_cards) == 3  # all three, severity-sorted
    assert wd.top_cards[0]["entity"] == "COSCO"
    assert len(wd.sanctions) == 1
    assert wd.sanctions[0]["entity"] == "COSCO"
    assert len(wd.market_deltas) == 1
    assert wd.market_deltas[0].symbol == "SNP"
    assert wd.market_deltas[0].pct_change == 4.0
    assert wd.scenario is not None
    assert "headline" in wd.scenario


@pytest.mark.asyncio
async def test_build_week_data_caps_top_cards_at_5(monkeypatch):
    fake_items = [
        {
            "severity": "HIGH",
            "entity": f"E{i}",
            "category": "sanctions",
            "fetched_at": f"2026-05-{18 - i:02d}T10:00:00Z",
        }
        for i in range(10)
    ]

    async def fake_builder(username):
        return fake_items, []

    monkeypatch.setattr("src.routers.risk_feed._build_live_feed_for_user", fake_builder)

    wd = await build_week_data("alice")
    assert len(wd.top_cards) == 5


# --- render_digest ---


def _sample_week_data() -> WeekData:
    return WeekData(
        username="alice",
        week_iso="2026-W21",
        week_start=datetime(2026, 5, 18, tzinfo=timezone.utc),
        week_end=datetime(2026, 5, 24, tzinfo=timezone.utc),
        top_cards=[
            {
                "severity": "HIGH",
                "entity": "COSCO",
                "source": "OFAC",
                "fetched_at": "2026-05-18T10:00:00Z",
                "synthesis": "New SDN designation announced today",
            },
        ],
        market_deltas=[TickerDelta("XOM", 100.0, 105.0, 5.0)],
        sanctions=[{"entity": "COSCO", "raw_text": "OFAC SDN designation"}],
        scenario={"headline": "Test scenario", "summary": "Test scenario body"},
        opening_synthesis="This is the LLM-generated opener.",
    )


def test_render_digest_html_includes_all_sections():
    wd = _sample_week_data()
    html, _text = render_digest(
        wd, preferences_url="https://x/prefs", unsubscribe_url="https://x/u"
    )
    assert "Weekly Brief" in html
    assert "This is the LLM-generated opener." in html
    assert "Your Watchlist This Week" in html
    assert "COSCO" in html
    assert "What Moved" in html
    assert "XOM" in html
    # Up/down phrasing replaced the +/- prefix for non-finance readers
    assert "up 5.0%" in html
    assert "Sanctions Roll-up" in html
    assert "Scenario Spotlight" in html
    assert "Test scenario" in html
    assert "Manage preferences" in html
    assert "Unsubscribe" in html


def test_render_digest_text_is_ascii_safe():
    wd = _sample_week_data()
    _html, text = render_digest(wd)
    assert "WEEKLY BRIEF" in text
    assert "COSCO" in text
    assert "XOM:" in text or "XOM" in text
    # No HTML tags in plain-text version
    assert "<html" not in text
    assert "<body" not in text


def test_render_digest_uppercases_lowercase_severity_in_html():
    """Regression: cards from the live pipeline have lowercase severity.
    The HTML template's [SEV] bracket must render uppercase. Surfaced by
    scripts/stub_e2e_demo.py on 2026-05-18."""
    wd = WeekData(
        username="alice",
        week_iso="2026-W21",
        week_start=datetime(2026, 5, 18, tzinfo=timezone.utc),
        week_end=datetime(2026, 5, 24, tzinfo=timezone.utc),
        top_cards=[
            {"severity": "critical", "entity": "COSCO", "synthesis": "x", "fetched_at": ""},
            {"severity": "high", "entity": "Sinopec", "synthesis": "y", "fetched_at": ""},
        ],
    )
    html, text = render_digest(wd)
    # HTML — uppercase brackets
    assert "[CRITICAL]" in html
    assert "[HIGH]" in html
    assert "[critical]" not in html
    assert "[high]" not in html
    # Plain text — same
    assert "[CRITICAL]" in text
    assert "[HIGH]" in text
    assert "[critical]" not in text
    assert "[high]" not in text


def test_render_digest_text_footer_links_on_separate_lines():
    """Regression: trim_blocks=True was stripping the newline between the
    'Manage preferences:' and 'Unsubscribe:' lines in the plain-text footer,
    concatenating them. Surfaced by scripts/stub_e2e_demo.py on 2026-05-18."""
    wd = _sample_week_data()
    _html, text = render_digest(
        wd, preferences_url="https://x/prefs", unsubscribe_url="https://x/u"
    )
    # The two links must be on separate lines, not concatenated
    assert "Manage preferences: https://x/prefs" in text
    assert "Unsubscribe: https://x/u" in text
    assert "https://x/prefsUnsubscribe" not in text
    # Check the actual separation: one of them must be followed by a newline
    # before the other appears.
    lines = text.splitlines()
    prefs_line = next((i for i, ln in enumerate(lines) if "Manage preferences" in ln), -1)
    unsub_line = next((i for i, ln in enumerate(lines) if "Unsubscribe" in ln), -1)
    assert prefs_line != -1 and unsub_line != -1
    assert prefs_line != unsub_line, "Both links ended up on the same line"


def test_render_digest_omits_empty_sections():
    wd = WeekData(
        username="alice",
        week_iso="2026-W21",
        week_start=datetime(2026, 5, 18, tzinfo=timezone.utc),
        week_end=datetime(2026, 5, 24, tzinfo=timezone.utc),
        # all section lists empty, no scenario, no synthesis
    )
    html, text = render_digest(wd)
    assert "Your Watchlist This Week" not in html
    assert "What Moved" not in html
    assert "Sanctions Roll-up" not in html
    assert "Scenario Spotlight" not in html
    assert "WATCHLIST" not in text


# --- send_weekly_digest gating ---


def test_send_weekly_digest_skipped_when_not_allowlisted(non_allowlisted_user):
    user = {**non_allowlisted_user, "email": "bob@example.com"}
    result = send_weekly_digest(user, _sample_week_data())
    assert result.status == "skipped_allowlist"


def test_send_weekly_digest_skipped_when_email_disabled(allowlisted_user):
    user = {**allowlisted_user, "email_enabled": 0}
    result = send_weekly_digest(user, _sample_week_data())
    assert result.status == "skipped_disabled"


def test_send_weekly_digest_skipped_when_no_email(allowlisted_user):
    user = {**allowlisted_user, "email": None}
    result = send_weekly_digest(user, _sample_week_data())
    assert result.status == "skipped_disabled"


def test_send_weekly_digest_skipped_kill_switch(allowlisted_user, monkeypatch):
    from src.notifications import clients as clients_mod

    clients_mod.get_sendgrid_client.cache_clear()
    result = send_weekly_digest(allowlisted_user, _sample_week_data())
    assert result.status == "skipped_kill_switch"


def test_send_weekly_digest_sends_and_logs(allowlisted_user, monkeypatch):
    fake_resp = MagicMock()
    fake_resp.headers = {"X-Message-Id": "sg-msg-id-abc123"}
    fake_client = MagicMock()
    fake_client.send.return_value = fake_resp

    monkeypatch.setattr(digest_mod, "get_sendgrid_client", lambda: fake_client)

    wd = _sample_week_data()
    result = send_weekly_digest(allowlisted_user, wd)

    assert result.status == "sent"
    assert result.provider_message_id == "sg-msg-id-abc123"

    fake_client.send.assert_called_once()
    sent_mail = fake_client.send.call_args.args[0]
    # Verify the Mail object has the right shape (SendGrid Mail is a complex object;
    # use string repr or its get() method for spot checks)
    mail_dict = sent_mail.get()
    assert mail_dict["subject"] == f"Weekly Brief - {len(wd.top_cards)} watchlist updates"
    assert mail_dict["personalizations"][0]["to"][0]["email"] == allowlisted_user["email"]

    # Verify log row landed
    from src.db import get_db

    conn = get_db()
    try:
        row = conn.execute(
            "SELECT username, channel, digest_week, provider_message_id, status "
            "FROM notification_log WHERE username = ? AND channel = 'email'",
            (allowlisted_user["username"],),
        ).fetchone()
    finally:
        conn.close()
    assert row["status"] == "sent"
    assert row["digest_week"] == "2026-W21"
    assert row["provider_message_id"] == "sg-msg-id-abc123"


def test_send_weekly_digest_failure_logs_error(allowlisted_user, monkeypatch):
    fake_client = MagicMock()
    fake_client.send.side_effect = RuntimeError("sendgrid: 503 service unavailable")

    monkeypatch.setattr(digest_mod, "get_sendgrid_client", lambda: fake_client)

    result = send_weekly_digest(allowlisted_user, _sample_week_data())
    assert result.status == "failed"
    assert "503" in result.error

    from src.db import get_db

    conn = get_db()
    try:
        row = conn.execute(
            "SELECT status, error_text, digest_week FROM notification_log "
            "WHERE username = ? AND channel = 'email'",
            (allowlisted_user["username"],),
        ).fetchone()
    finally:
        conn.close()
    assert row["status"] == "failed"
    assert "503" in row["error_text"]
    assert row["digest_week"] == "2026-W21"
