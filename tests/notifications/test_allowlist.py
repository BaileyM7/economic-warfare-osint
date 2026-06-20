"""Regression guard for the NOTIFICATIONS_ALLOWLIST contract (Phase 3 bugfix).

Empty allowlist = allow everyone enrolled (the documented contract); a non-empty
allowlist gates to exactly those usernames. The old code returned False for
everyone when empty, silently skipping every scheduled brief.
"""

from __future__ import annotations

from src.common.config import config
from src.notifications.clients import is_user_allowlisted


def test_empty_allowlist_allows_everyone(monkeypatch):
    monkeypatch.setattr(config, "notifications_allowlist", "", raising=True)
    assert is_user_allowlisted("anyone") is True
    monkeypatch.setattr(config, "notifications_allowlist", "   ", raising=True)
    assert is_user_allowlisted("anyone") is True


def test_nonempty_allowlist_gates(monkeypatch):
    monkeypatch.setattr(config, "notifications_allowlist", "alice, bob", raising=True)
    assert is_user_allowlisted("alice") is True
    assert is_user_allowlisted("bob") is True
    assert is_user_allowlisted("carol") is False
