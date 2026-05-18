"""Lazy client factories for Twilio (SMS) and SendGrid (email).

Both factories return None when notifications are disabled or the relevant
credentials are missing — this dual guard means downstream code that calls
`client = get_twilio_client(); if client is None: ...` will fail safely
in dev, in CI, and on Render before secrets are provisioned.
"""

from __future__ import annotations

from functools import lru_cache

from sendgrid import SendGridAPIClient
from twilio.rest import Client as TwilioClient

from src.common.config import config


@lru_cache(maxsize=1)
def get_twilio_client() -> TwilioClient | None:
    """Return a configured Twilio client, or None if disabled/unconfigured."""
    if not config.notifications_enabled or not config.twilio_account_sid:
        return None
    return TwilioClient(config.twilio_account_sid, config.twilio_auth_token)


@lru_cache(maxsize=1)
def get_sendgrid_client() -> SendGridAPIClient | None:
    """Return a configured SendGrid client, or None if disabled/unconfigured."""
    if not config.notifications_enabled or not config.sendgrid_api_key:
        return None
    return SendGridAPIClient(config.sendgrid_api_key)


def is_user_allowlisted(username: str) -> bool:
    """True iff `username` is in NOTIFICATIONS_ALLOWLIST (comma-separated env)."""
    raw = config.notifications_allowlist or ""
    allowed = {u.strip() for u in raw.split(",") if u.strip()}
    return username in allowed
