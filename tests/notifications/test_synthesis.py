"""Tests for the LLM opening synthesis (with deterministic fallback)."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock

from src.notifications.email_digest import WeekData
from src.notifications.synthesis import generate_opening_synthesis


def _make_week_data(cards=None) -> WeekData:
    return WeekData(
        username="alice",
        week_iso="2026-W21",
        week_start=datetime(2026, 5, 18, tzinfo=timezone.utc),
        week_end=datetime(2026, 5, 24, tzinfo=timezone.utc),
        top_cards=cards or [],
    )


def test_synthesis_uses_fallback_when_client_is_none():
    wd = _make_week_data(
        cards=[
            {"severity": "HIGH", "entity": "COSCO"},
            {"severity": "MEDIUM", "entity": "Sinopec"},
            {"severity": "LOW", "entity": "PetroChina"},
        ]
    )
    text = generate_opening_synthesis(wd, anthropic_client=None)
    assert "watchlist" in text.lower()
    assert "3 updates" in text
    assert "1 of them high-severity" in text
    assert "COSCO" in text
    # Should NOT contain the LLM prompt markers
    assert "Output only" not in text


def test_synthesis_fallback_with_no_cards():
    wd = _make_week_data(cards=[])
    text = generate_opening_synthesis(wd, anthropic_client=None)
    assert "0 updates" in text
    assert "(none)" in text


def test_synthesis_uses_llm_when_client_present():
    wd = _make_week_data(cards=[{"severity": "HIGH", "entity": "COSCO"}])

    fake_block = MagicMock()
    fake_block.text = "  COSCO took the spotlight this week with a fresh OFAC SDN designation.  "
    fake_resp = MagicMock()
    fake_resp.content = [fake_block]

    fake_client = MagicMock()
    fake_client.messages.create.return_value = fake_resp

    text = generate_opening_synthesis(wd, anthropic_client=fake_client)

    assert text == "COSCO took the spotlight this week with a fresh OFAC SDN designation."
    fake_client.messages.create.assert_called_once()
    # Verify the prompt was templated with the right inputs
    call_kwargs = fake_client.messages.create.call_args.kwargs
    prompt_text = call_kwargs["messages"][0]["content"]
    assert "1 updates" in prompt_text
    assert "COSCO" in prompt_text


def test_synthesis_falls_back_when_llm_raises():
    wd = _make_week_data(cards=[{"severity": "HIGH", "entity": "COSCO"}])

    fake_client = MagicMock()
    fake_client.messages.create.side_effect = RuntimeError("rate limit")

    text = generate_opening_synthesis(wd, anthropic_client=fake_client)
    assert "watchlist" in text.lower()
    assert "COSCO" in text  # fallback still names the top entity


def test_synthesis_falls_back_when_llm_returns_empty(caplog):
    wd = _make_week_data(cards=[{"severity": "HIGH", "entity": "COSCO"}])

    fake_block = MagicMock()
    fake_block.text = "   "  # whitespace only
    fake_resp = MagicMock()
    fake_resp.content = [fake_block]

    fake_client = MagicMock()
    fake_client.messages.create.return_value = fake_resp

    text = generate_opening_synthesis(wd, anthropic_client=fake_client)
    assert "watchlist" in text.lower()
    assert "empty synthesis" in caplog.text.lower()
