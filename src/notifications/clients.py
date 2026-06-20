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
def get_twilio_client():
    """Return a configured Twilio client, or None if disabled/unconfigured.

    Returns a StubTwilioClient when TWILIO_STUB_MODE is enabled — useful for
    local testing without burning real Twilio credit. The kill-switch
    (NOTIFICATIONS_ENABLED=false) still applies to the stub.

    Return type is intentionally untyped here: it's a union of TwilioClient,
    StubTwilioClient, and None. Callers only touch `.messages.create(...)`
    which both real and stub satisfy duck-typed, so a strict union annotation
    would force a TYPE_CHECKING import dance for zero downstream payoff.
    """
    if not config.notifications_enabled:
        return None
    if config.twilio_stub_mode:
        from src.notifications.stub_client import StubTwilioClient

        return StubTwilioClient(
            account_sid=config.twilio_account_sid or "ACstubaccountsid0000000000000000"
        )
    if not config.twilio_account_sid:
        return None
    return TwilioClient(config.twilio_account_sid, config.twilio_auth_token)


@lru_cache(maxsize=1)
def get_sendgrid_client():
    """Return a configured SendGrid client, or None if disabled/unconfigured.

    Returns a StubSendGridClient when SENDGRID_STUB_MODE is enabled -- useful
    for local testing without burning the free-tier quota. The kill-switch
    (NOTIFICATIONS_ENABLED=false) still applies to the stub.

    Return type is intentionally untyped here: it's a union of SendGridAPIClient,
    StubSendGridClient, and None. Callers only touch `.send(mail)` which both
    real and stub satisfy duck-typed.
    """
    if not config.notifications_enabled:
        return None
    if config.sendgrid_stub_mode:
        from src.notifications.stub_client_sendgrid import StubSendGridClient

        return StubSendGridClient(api_key=config.sendgrid_api_key or "SG.stub-api-key")
    if not config.sendgrid_api_key:
        return None
    return SendGridAPIClient(config.sendgrid_api_key)


def is_user_allowlisted(username: str) -> bool:
    """True iff `username` may receive notifications under NOTIFICATIONS_ALLOWLIST.

    Per the documented contract (.env.example: "empty = no allowlist enforced"),
    an EMPTY allowlist allows everyone enrolled; a NON-EMPTY one gates to exactly
    those comma-separated usernames.

    Bugfix (Phase 3): this previously returned False for EVERYONE when the
    allowlist was empty — which silently skipped every scheduled brief
    (status="skipped_allowlist"), the real reason "scheduled messages weren't
    sending". Empty now correctly means no restriction.
    """
    raw = (config.notifications_allowlist or "").strip()
    if not raw:
        return True  # empty = no restriction (matches the documented contract)
    allowed = {u.strip() for u in raw.split(",") if u.strip()}
    return username in allowed
