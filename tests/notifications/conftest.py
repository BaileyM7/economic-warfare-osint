"""Fixtures for notification tests."""

from __future__ import annotations

import pytest

from src.db import get_db, init_db


@pytest.fixture
def fresh_db():
    """Wipe + reinit the SQLite test DB before each test.

    The session-level `tests/conftest.py` already redirected DB_PATH to a
    temp file. This fixture ensures notification tests get a clean slate
    even if other tests (or earlier runs) left rows behind.
    """
    from src import db as _db

    if _db.DB_PATH.exists():
        _db.DB_PATH.unlink()
    init_db()
    yield


@pytest.fixture
def allowlisted_user(monkeypatch, fresh_db):
    """A user with SMS enabled + phone number, in the allowlist."""
    user = {
        "username": "alice",
        "email": "alice@example.com",
        "phone_number": "+15005550006",  # Twilio magic number (valid format)
        "sms_enabled": 1,
        "email_enabled": 1,
    }
    # Seed the user row so DB queries find them.
    conn = get_db()
    try:
        conn.execute(
            "INSERT INTO users (username, email, phone_number, sms_enabled, email_enabled) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                user["username"],
                user["email"],
                user["phone_number"],
                user["sms_enabled"],
                user["email_enabled"],
            ),
        )
        conn.commit()
    finally:
        conn.close()

    # Put alice in the allowlist via the config singleton's underlying env var.
    # is_user_allowlisted reads config.notifications_allowlist each call, so we
    # just need to mutate the field on the live singleton (the dataclass field
    # is plain; no recomputation needed).
    from src.common.config import config as _config

    monkeypatch.setattr(_config, "notifications_allowlist", "alice", raising=True)
    return user


@pytest.fixture
def non_allowlisted_user(fresh_db):
    """A user that exists but is NOT in the allowlist."""
    user = {
        "username": "bob",
        "phone_number": "+15005550006",
        "sms_enabled": 1,
        "email_enabled": 0,
    }
    conn = get_db()
    try:
        conn.execute(
            "INSERT INTO users (username, phone_number, sms_enabled, email_enabled) "
            "VALUES (?, ?, ?, ?)",
            (user["username"], user["phone_number"], user["sms_enabled"], user["email_enabled"]),
        )
        conn.commit()
    finally:
        conn.close()
    return user


@pytest.fixture
def sample_card():
    return {
        "id": "card-cosco-2026-05-18-001",
        "severity": "HIGH",
        "entity": "COSCO",
        "synthesis": "New OFAC SDN designation citing IRGC ties announced today",
        "short_url": "ew.app/r/x9k",
    }
